import asyncio
import json

from approval_preferences import start_approval_control_server
from server import WsBridge, WsSessionHandler
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


async def request(port, *, method="GET", token=None, origin=None, body=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    payload = body or b""
    headers = [f"{method} /approval-preferences HTTP/1.1", "Host: 127.0.0.1"]
    if token:
        headers.append(f"Authorization: Bearer {token}")
    if origin is not None:
        headers.append(f"Origin: {origin}")
    if method == "POST":
        headers.extend(["Content-Type: application/json", f"Content-Length: {len(payload)}"])
    writer.write(("\r\n".join(headers) + "\r\nConnection: close\r\n\r\n").encode() + payload)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, raw = response.split(b"\r\n\r\n", 1)
    status = int(head.split(b" ", 2)[1])
    return status, json.loads(raw) if raw else None, response


def test_control_server_requires_private_bearer_and_rejects_browser_origins():
    with temporary_agent_paths(prefix="approval-control-auth-") as paths:
        async def exercise():
            server, _token, port = await start_approval_control_server(paths, token="a" * 64)
            try:
                assert (await request(port))[0] == 401
                assert (await request(port, token="b" * 64))[0] == 401
                assert (await request(port, token="a" * 64, origin="http://localhost:5173"))[0] == 403
                status, payload, raw = await request(port, token="a" * 64)
                assert status == 200
                assert payload["type"] == "approval_preferences.state"
                assert "a" * 64 not in raw.decode("utf-8")
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(exercise())


def test_control_server_accepts_authenticated_set_and_returns_no_credential():
    with temporary_agent_paths(prefix="approval-control-set-") as paths:
        async def exercise():
            server, _token, port = await start_approval_control_server(paths, token="c" * 64)
            try:
                status, payload, raw = await request(
                    port, method="POST", token="c" * 64,
                    body=json.dumps({"type": "approval_preferences.set", "approval_mode": "auto"}).encode(),
                )
                assert status == 200
                assert payload["type"] == "approval_preferences.done"
                assert payload["approval_mode"] == "auto"
                assert "c" * 64 not in raw.decode("utf-8")
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(exercise())


def test_websocket_write_is_denied_even_when_origin_is_allowlisted():
    with temporary_agent_paths(prefix="approval-control-ws-") as paths:
        events = []
        bridge = WsBridge(emit=events.append, paths=paths)
        bridge.client_origin = "file://"
        handler = WsSessionHandler(paths)
        repl = type("Repl", (), {"session": create_new(paths, conversation_id="approval-control-ws")})()
        asyncio.run(handler._dispatch({
            "type": "approval_preferences.set", "approval_mode": "full",
        }, repl, bridge))
        assert events[-1]["type"] == "approval_preferences.done"
        assert events[-1]["status"] == "rejected"
        assert "Electron 主进程" in events[-1]["message"]
