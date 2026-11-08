import asyncio
import ast
import unittest

import websockets
from websockets.asyncio.server import serve

from pathlib import Path
import sys
CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import server


class WebSocketOriginPolicyTests(unittest.TestCase):
    def test_local_electron_and_dev_origins_are_allowed_but_remote_origins_are_not(self):
        policy = server._websocket_origin_policy()
        self.assertIn(None, policy)  # native CLI clients do not send Origin
        self.assertIn("file://", policy)  # confirmed Electron loadFile origin
        for origin in (
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://[::1]:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://[::1]:5174",
        ):
            self.assertIn(origin, policy)
        for origin in (
            "https://localhost:5173",
            "http://localhost:3000",
            "http://localhost:5175",
            "http://127.0.0.1:8765",
            "https://attacker.example",
            "http://localhost.attacker.example",
            "http://192.168.1.5:5173",
            "null",
            "file://attacker.example",
        ):
            self.assertNotIn(origin, policy)

    def test_both_server_modes_apply_origin_policy(self):
        source = server.__file__
        tree = ast.parse(Path(source).read_text(encoding="utf-8"))
        required = {"_run_managed_application_server", "run_server"}
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in required:
                continue
            for item in ast.walk(node):
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "serve":
                    origins = next((kw.value for kw in item.keywords if kw.arg == "origins"), None)
                    self.assertIsInstance(origins, ast.Call)
                    self.assertIsInstance(origins.func, ast.Name)
                    self.assertEqual(origins.func.id, "_websocket_origin_policy")
                    found.add(node.name)
        self.assertEqual(found, required)
    def test_websocket_handshake_rejects_remote_origin_and_allows_cli_and_local_dev(self):
        async def exercise():
            async def handler(ws):
                await ws.send("accepted")

            async with serve(handler, "127.0.0.1", 0, origins=server._websocket_origin_policy()) as srv:
                port = srv.sockets[0].getsockname()[1]
                async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                    self.assertEqual(await ws.recv(), "accepted")
                for origin in ("http://localhost:5174", "file://"):
                    async with websockets.connect(f"ws://127.0.0.1:{port}", origin=origin) as ws:
                        self.assertEqual(await ws.recv(), "accepted")
                for origin in ("https://attacker.example", "http://localhost:3000", "null"):
                    with self.assertRaises(websockets.exceptions.InvalidStatus):
                        async with websockets.connect(f"ws://127.0.0.1:{port}", origin=origin):
                            pass

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
