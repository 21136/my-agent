"""Synthetic localhost-only fixture for unified UI acceptance; never reads user data."""
import sys, os, pathlib, asyncio, json
root = pathlib.Path(sys.argv[1]).resolve()
core = pathlib.Path(sys.argv[2]).resolve()
read_roots = [root, core, pathlib.Path(sys.prefix).resolve(), pathlib.Path(sys.base_prefix).resolve()]
def within(path, roots):
    try:
        p = pathlib.Path(path).resolve()
        return any(p == r or r in p.parents for r in roots)
    except (TypeError, ValueError, OSError):
        return False
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        mode, flags = args[1:]
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
        if not within(args[0], [root] if writing else read_roots): raise PermissionError('fixture: filesystem denied')
    if event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.replace'):
        targets = args[:2] if event in ('os.rename', 'os.replace') else args[:1]
        if any(not within(p, [root]) for p in targets): raise PermissionError('fixture: filesystem mutation denied')
    if event in ('socket.connect', 'socket.bind') and isinstance(args[1], tuple) and args[1][0] not in ('127.0.0.1', '::1'):
        raise PermissionError('fixture: external network denied')
sys.dont_write_bytecode = True
sys.addaudithook(audit)
sys.path.insert(0, str(core))
from types import SimpleNamespace
from paths import AgentPaths
from session import Session, SessionMeta
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from websockets.asyncio.server import serve
import server, llm_client, work_records_service, work_records_luna_intent
from llm_models import ModelEntry
paths = AgentPaths(root, root / 'workspace', root / 'data', root / 'evolve')
# Unified-shell mount and turn completion request services.list. This E2E
# does not test service management, so provide an isolated empty-list tool
# instead of letting the synthetic AgentPaths root emit a misleading error.
run_service_main = paths.evolve / 'tools' / 'common' / 'run_service' / 'main.py'
run_service_main.parent.mkdir(parents=True, exist_ok=True)
run_service_main.write_text(
    "def run_service(payload):\n"
    "    if payload.get('action') == 'list':\n"
    "        return {'ok': True, 'services': []}\n"
    "    return {'ok': False, 'error': 'synthetic fixture supports list only'}\n",
    encoding='utf-8',
)
session_dir = paths.data / 'sessions' / 'synthetic-session'
session_dir.mkdir(parents=True)
session = Session('synthetic-session', session_dir, '', SessionMeta(), [], paths)
executor_session = ExecutorSession(session_dir=session_dir)
executor = ToolExecutor(registry=ToolRegistry(agent_paths=paths, evolved=[]), session=executor_session, confirm_fn=lambda *_a, **_k: 'y')
agent = SimpleNamespace(session=session, executor=executor, work_card_reference_intent=None)
agent.bind_host_run_context = lambda run_id, _authorization='': setattr(executor_session, 'host_run_id', run_id)
repl = SimpleNamespace(session=session, agent=agent, last_turn_finish_reason=None)
server.resume_desktop_or_create = lambda _: session
def build(_session, _paths, bridge, **_kwargs):
    bridge._session_provider = lambda: repl.session
    def handle_line(line):
        executor.begin_turn()
        bridge.emit_assistant('合成普通回复')
        return 'continue'
    repl.handle_line = handle_line
    return repl
server._build_repl = build
server.WsBridge.emit_session_state = lambda bridge, s: bridge.emit({'type':'session.banner', 'session_id':s.conversation_id, 'topics':[], 'turn_mode':'agent', 'turn_mode_label':'', 'phase':'S4'})
server.emit_corruption_notices = lambda *_a: None
server._emit_pending_work_card_references = lambda *_a: None
class Registry:
    def get(self, _): return SimpleNamespace(id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=96000)
work_records_service.get_registry = lambda *_a, **_k: Registry()
work_records_luna_intent.get_registry = lambda *_a, **_k: Registry()
entry = ModelEntry(id='0x567-flash', name='synthetic', vendor='0x567', base_url='https://synthetic.invalid', provider_model='gpt-6-luna')
server._auto_recognition_target = lambda _paths: (entry, entry.chat_completions_url())
class FakeClassifier:
    paths = None
    def set_cancel_event(self, event): self.cancel_event = event
    def chat_confirmed_json(self, messages, *, model_id, session_id, expected_preview_json, confirm_fn):
        if confirm_fn(expected_preview_json, False) != 'y': raise RuntimeError('denied')
        text = json.loads(messages[1]['content'])['text']
        if text == '合成闲聊':
            return json.dumps({'action':'none','kind':'qa','title':None,'requirement':None,'scope':None,'item_id':None})
        name = '合成拒绝事项' if text == '合成拒绝任务' else '合成保存事项'
        return json.dumps({'action':'record','kind':'requirement','title':name,'requirement':'合成要求内容','scope':'independent','item_id':None}, ensure_ascii=False)
llm_client.LLMClient = lambda **_kwargs: FakeClassifier()
async def main():
    async with serve(server.WsSessionHandler(paths).handle, '127.0.0.1', 0) as ws:
        port=ws.sockets[0].getsockname()[1]
        if port in (5173,5174,8000,8765): raise RuntimeError('default port forbidden')
        print(json.dumps({'port':port,'session_id':session.conversation_id}), flush=True)
        await asyncio.to_thread(sys.stdin.buffer.readline)
asyncio.run(main())
