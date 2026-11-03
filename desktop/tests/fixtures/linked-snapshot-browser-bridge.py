"""Synthetic-only bridge: real work-card publication and WsSessionHandler dispatch."""
import asyncio
import json
import os
from pathlib import Path
import site
import sys

root = Path(sys.argv[1]).resolve(strict=True)
core = Path(sys.argv[2]).resolve(strict=True)
assert Path(os.environ['MY_AGENT_ROOT']).resolve(strict=True) == root
assert Path(os.environ['TEMP']).resolve(strict=True).is_relative_to(root)
assert str(site.getusersitepackages()) not in sys.path
sys.path[:0] = [str(core), str(core / 'tests')]
from paths import AgentPaths
paths = AgentPaths.discover()
assert paths.agent_root == root and paths.workspace == root/'workspace' and paths.data == root/'data' and paths.evolve == root/'evolve'
from test_linked_project_session_delivery import setup_copy
from work_card_presentations import WorkCardPresentations
from work_card_ledger import WorkCardLedger
from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
import server
from websockets.asyncio.server import serve

parent, bindings, rows, store, events, bridge, repl = setup_copy(root)
assert parent.paths.agent_root == root and parent.session_dir.resolve().is_relative_to(root)
assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 1
present = next(event for event in events if event.get('type') == 'work-card.present')
pid, card = present['presentation_id'], present['card_id']
ledger = WorkCardLedger(parent, store)
presentations = WorkCardPresentations(parent, store, ledger)
evidence = LinkedSnapshotViewEvidence(parent, store, ledger)
for target in (presentations._presentations_store._path, evidence._evidence._path, ledger._ledger_store._path):
    assert target.resolve().is_relative_to(root)

async def main():
    async def socket_handler(socket):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        def emit(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)
        live_bridge = server.WsBridge(emit=emit, paths=parent.paths)
        handler = server.WsSessionHandler(parent.paths)
        # The synthetic Session created by the existing test fixture is in memory,
        # not registered in the desktop session index. Keep the real sender and
        # its offer lock, but bind its accounting to this exact synthetic store.
        def account_offer(event):
            assert event['session_id'] == parent.conversation_id and event['presentation_id'] == pid and event['card_id'] == card
            assert presentations.mark_transport_offer(pid, card).status == 'offered'
        handler._mark_work_card_transport_offer = account_offer
        sender = asyncio.create_task(handler._sender(socket, queue, live_bridge))
        try:
            emit({'type': 'session.banner', 'session_id': parent.conversation_id})
            server._emit_pending_work_card_references(repl, live_bridge)
            async for frame in socket:
                message = json.loads(frame)
                if message.get('type') not in {'work-card.received','work-card.snapshot.view'}:
                    continue
                await handler._handle_incoming(message, repl, live_bridge)
        finally:
            await queue.put(None)
            await sender
    async with serve(socket_handler, '127.0.0.1', 0) as listener:
        print(json.dumps({'port':listener.sockets[0].getsockname()[1], 'session':parent.conversation_id,
            'presentation_id':pid, 'card_id':card, 'receipt_path':str(presentations._presentations_store._path),
            'evidence_path':str(evidence._evidence._path), 'root':str(root)}), flush=True)
        await asyncio.to_thread(sys.stdin.buffer.readline)

asyncio.run(main())
