"""Synthetic-only real-model fixture for the opt-in local auto-recognition E2E."""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import socket
import sys
import tempfile
from types import SimpleNamespace

from auto_recognition_local_live_guard import confirmed_request_matches, fixture_root_is_safe, live_provider_requested

root = pathlib.Path(sys.argv[1]).resolve()
core = pathlib.Path(sys.argv[2]).resolve()
temp_root = pathlib.Path(sys.argv[3]).resolve()
try:
    live_provider = live_provider_requested(sys.argv[4:])
except ValueError:
    raise SystemExit(2)
sandbox_root = core.parent
if not fixture_root_is_safe(root, temp_root):
    raise SystemExit(2)
if sandbox_root.name != 'sandbox' or sandbox_root.parent.name != '.worktrees' or core != (sandbox_root / 'agent-core').resolve():
    print(json.dumps({'error': 'unsafe_fixture_source'}), flush=True)
    raise SystemExit(2)
sys.dont_write_bytecode = True
sys.path.insert(0, str(core))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from paths import AgentPaths
from llm_models import ModelEntry, ModelRegistry
from session import Session, SessionMeta
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from websockets.asyncio.server import serve
import server
import llm_client
import work_records_service
import work_records_luna_intent

entry = ModelEntry(
    id="0x567-flash", name="0x567 gpt-6-luna", vendor="0x567",
    base_url="https://api-cdn.0x567.com", provider_model="gpt-6-luna",
    api_key_env="OX567_API_KEY", max_input_tokens=96000,
)
provider_host = "api-cdn.0x567.com"
provider_ips: set[str] = set()
if live_provider:
    # Resolve the secret only in the explicitly opted-in provider run. It is
    # never copied to argv, stdout/stderr, a fixture file, or the child env.
    provider_paths = AgentPaths.from_root(sandbox_root)
    api_key = entry.resolve_api_key(provider_paths)
    if not api_key:
        print(json.dumps({"error": "credential_unavailable"}), flush=True)
        raise SystemExit(2)
    os.environ["OX567_API_KEY"] = api_key
    os.environ["LLM_TIMEOUT_SEC"] = "25"
    # Resolve only the one permitted provider before installing the network fence.
    provider_ips = {
        info[4][0]
        for info in socket.getaddrinfo(provider_host, 443, type=socket.SOCK_STREAM)
    }
    if not provider_ips:
        print(json.dumps({"error": "provider_dns_unavailable"}), flush=True)
        raise SystemExit(2)

read_roots = [root, core, pathlib.Path(sys.prefix).resolve(), pathlib.Path(sys.base_prefix).resolve()]
read_roots.extend(pathlib.Path(p).resolve() for p in sys.path if p and pathlib.Path(p).exists())

def within(path: object, roots: list[pathlib.Path]) -> bool:
    try:
        candidate = pathlib.Path(os.fsdecode(path)).resolve()
        return any(candidate == base or base in candidate.parents for base in roots)
    except (TypeError, ValueError, OSError):
        return False

def audit(event: str, args: tuple[object, ...]) -> None:
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        mode = args[1] if len(args) > 1 else "r"
        flags = args[2] if len(args) > 2 else 0
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        )
        if not within(args[0], [root] if writing else read_roots):
            raise PermissionError("synthetic fixture filesystem fence")
    if event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace"):
        targets = args[:2] if event in ("os.rename", "os.replace") else args[:1]
        if any(not within(target, [root]) for target in targets):
            raise PermissionError("synthetic fixture filesystem mutation fence")
    if event == "socket.getaddrinfo":
        host = args[0] if args else None
        allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        if live_provider:
            allowed_hosts.add(provider_host)
        if host not in allowed_hosts:
            raise PermissionError("synthetic fixture DNS fence")
    if event == "socket.bind":
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise PermissionError("synthetic fixture bind fence")
    if event == "socket.connect":
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple):
            raise PermissionError("synthetic fixture connect fence")
        host, port = address[0], address[1]
        if host in ("127.0.0.1", "::1", "localhost"):
            return
        if host not in provider_ips or port != 443:
            raise PermissionError("synthetic fixture connect fence")

sys.addaudithook(audit)

paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
for directory in (paths.workspace, paths.data, paths.evolve):
    directory.mkdir(parents=True, exist_ok=True)

# The mounted shell asks for service inventory during a turn. Keep that path
# deterministic and synthetic; do not start or inspect any installed service.
run_service_main = paths.evolve / "tools" / "common" / "run_service" / "main.py"
run_service_main.parent.mkdir(parents=True, exist_ok=True)
run_service_main.write_text(
    "def run_service(payload):\n"
    "    return {'ok': True, 'services': []} if payload.get('action') == 'list' else {'ok': False}\n",
    encoding="utf-8",
)
session_id = "synthetic-auto-live-session"
session_id_expected = session_id
session_dir = paths.data / "sessions" / session_id
session_dir.mkdir(parents=True)
session = Session(session_id, session_dir, "", SessionMeta(), [], paths)
executor_session = ExecutorSession(session_dir=session_dir)
executor = ToolExecutor(
    registry=ToolRegistry(agent_paths=paths, evolved=[]),
    session=executor_session,
    confirm_fn=lambda *_args, **_kwargs: "n",
)
agent = SimpleNamespace(session=session, executor=executor, work_card_reference_intent=None)
agent.bind_host_run_context = lambda run_id, _authorization="": setattr(executor_session, "host_run_id", run_id)
repl = SimpleNamespace(session=session, agent=agent, last_turn_finish_reason=None)
server.resume_desktop_or_create = lambda _paths: session

def build(_session, _paths, bridge, **_kwargs):
    bridge._session_provider = lambda: repl.session
    def handle_line(_line):
        executor.begin_turn()
        bridge.emit_assistant("合成普通回复")
        return "continue"
    repl.handle_line = handle_line
    return repl

server._build_repl = build
server.WsBridge.emit_session_state = lambda bridge, current: bridge.emit({
    "type": "session.banner", "session_id": current.conversation_id,
    "topics": [], "turn_mode": "agent", "turn_mode_label": "", "phase": "S4",
})
server.emit_corruption_notices = lambda *_args: None
server._emit_pending_work_card_references = lambda *_args: None
server._auto_recognition_target = lambda _paths: (entry, entry.chat_completions_url())
registry = ModelRegistry((entry,), entry.id, entry.id, {"0x567-flash": entry.id})
llm_client.get_registry = lambda *_args, **_kwargs: registry
work_records_service.get_registry = lambda *_args, **_kwargs: registry
work_records_luna_intent.get_registry = lambda *_args, **_kwargs: registry

expected_user_text = '\u8bf7\u628a\u5408\u6210\u7b80\u5386\u4e2d\u7684\u4e2a\u4eba\u4ecb\u7ecd\u7cbe\u7b80\u4e3a\u4e09\u53e5\u8bdd\u3002\u8fd9\u662f\u4e00\u4e2a\u72ec\u7acb\u4e8b\u9879\u3002'
if work_records_service.is_explicit_record_request(expected_user_text):
    print(json.dumps({'error': 'synthetic_payload_hits_explicit_capture_gate'}), flush=True)
    raise SystemExit(2)

def validate_outbound_preview(preview):
    prefix = (
        "工作记录单次外发确认\n"
        "本次仅授权把以下完整消息发送给 0x567-flash（服务端模型 gpt-6-luna）做只读分类；"
        "拒绝/取消/超时均不会调用模型或写入记录。此授权仅对应本次确认，不会跨回合、切换会话或重启继承。\n"
        "精确外发消息：\n"
    )
    if not isinstance(preview, str) or not preview.startswith(prefix):
        return False
    try:
        payload = json.loads(preview[len(prefix):])
        probe = work_records_luna_intent.WorkRecordsLunaIntent(SimpleNamespace(chat=lambda *_a, **_k: None))
        return payload == probe._messages(expected_user_text, [])
    except Exception:
        return False
provider_calls = {"count": 0, "http_posts": 0}
# Count and fence the actual HTTP dispatch, not merely entry to the wrapper.
# Guard permits only the frozen synthetic payload and the designated endpoint.
import httpx
_original_post = httpx.Client.post
def guarded_post(client, url, **kwargs):
    probe = work_records_luna_intent.WorkRecordsLunaIntent(SimpleNamespace(chat=lambda *_a, **_k: None))
    from auto_recognition_local_live_guard import expected_safe_messages, confirmed_preview
    _, safe = expected_safe_messages(probe, expected_user_text)
    payload = confirmed_preview(entry, session_id_expected, safe)["provider_payload"]
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if (provider_calls["count"] != 1 or provider_calls["http_posts"] != 0
            or str(url) != entry.chat_completions_url() or kwargs.get("content") != body
            or kwargs.get("follow_redirects") is not False):
        raise RuntimeError("synthetic HTTP dispatch refused")
    provider_calls["http_posts"] += 1
    return _original_post(client, url, **kwargs)
httpx.Client.post = guarded_post
_real_llm_client = llm_client.LLMClient

class GuardedRealLLMClient(_real_llm_client):
    """Allow exactly one confirmed request with the exact synthetic payload."""
    def chat(self, *_args, **_kwargs):
        raise RuntimeError("unconfirmed model request refused")

    def chat_confirmed_json_batch(self, *_args, **_kwargs):
        raise RuntimeError("batch model request refused")

    def chat_confirmed_json(self, messages, *, model_id, session_id, expected_preview_json, confirm_fn):
        if provider_calls["count"] or model_id != entry.id or session_id != session_id_expected:
            raise RuntimeError("synthetic confirmed-call envelope refused")
        if not confirmed_request_matches(
            messages, model_id=model_id, session_id=session_id,
            expected_preview_json=expected_preview_json, entry=entry,
            intent=work_records_luna_intent.WorkRecordsLunaIntent(
                SimpleNamespace(chat=lambda *_a, **_k: None)
            ), expected_user_text=expected_user_text,
        ):
            raise RuntimeError("non-synthetic confirmed-call envelope refused")
        provider_calls["count"] += 1

        def exact_confirmation(shown, allow_approve_all):
            if shown != expected_preview_json or allow_approve_all is not False:
                raise RuntimeError("unexpected model-request confirmation refused")
            return confirm_fn(shown, allow_approve_all)

        return super().chat_confirmed_json(
            messages, model_id=model_id, session_id=session_id,
            expected_preview_json=expected_preview_json, confirm_fn=exact_confirmation,
        )

llm_client.LLMClient = GuardedRealLLMClient
server.LLMClient = GuardedRealLLMClient

async def main() -> None:
    async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as websocket_server:
        port = websocket_server.sockets[0].getsockname()[1]
        if port in (5173, 5174, 8000, 8765):
            raise RuntimeError("reserved port selected")
        print(json.dumps({"port": port, "session_id": session_id}), flush=True)
        while True:
            command = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not command:
                return
            if command.strip() == b"verify":
                print(json.dumps({"model_calls": provider_calls["count"], "http_posts": provider_calls["http_posts"]}), flush=True)
            elif command.strip() == b"stop":
                return
            else:
                try:
                    request = json.loads(command)
                    if request.get("op") == "validate_preview":
                        allowed = validate_outbound_preview(request.get("preview"))
                        print(json.dumps({"preview_allowed": allowed}), flush=True)
                    else:
                        print(json.dumps({"error": "unknown_fixture_command"}), flush=True)
                except Exception:
                    print(json.dumps({"error": "invalid_fixture_command"}), flush=True)
asyncio.run(main())








