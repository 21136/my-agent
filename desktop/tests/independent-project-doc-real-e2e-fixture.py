"""Local-only real websocket fixture for independent project-document UI acceptance."""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
core = pathlib.Path(sys.argv[2]).resolve()
read_roots = [root, core, pathlib.Path(sys.prefix).resolve(), pathlib.Path(sys.base_prefix).resolve()]


def within(path, roots):
    try:
        candidate = pathlib.Path(path).resolve()
        return any(candidate == allowed or allowed in candidate.parents for allowed in roots)
    except (TypeError, ValueError, OSError):
        return False


def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        mode, flags = args[1:]
        writing = (isinstance(mode, str) and any(char in mode for char in "wax+")) or (
            isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        )
        if not within(args[0], [root] if writing else read_roots):
            raise PermissionError("fixture filesystem boundary denied")
    if event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace"):
        targets = args[:2] if event in ("os.rename", "os.replace") else args[:1]
        if any(not within(target, [root]) for target in targets):
            raise PermissionError("fixture filesystem mutation denied")
    if event in ("socket.connect", "socket.bind") and isinstance(args[1], tuple):
        if args[1][0] not in ("127.0.0.1", "::1"):
            raise PermissionError("fixture network boundary denied")


sys.dont_write_bytecode = True
sys.addaudithook(audit)
sys.path.insert(0, str(core))

from paths import AgentPaths
from session import Session, SessionMeta
from websockets.asyncio.server import serve
import server

paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
for directory in (paths.workspace, paths.data, paths.evolve):
    directory.mkdir(parents=True, exist_ok=True)
(paths.evolve / "_index.core.toml").write_text("", encoding="utf-8")

project_id = "synthetic-independent-doc-project"
project_root = paths.workspace / project_id
project_root.mkdir(parents=True)
(project_root / "PROJECT.md").write_text("# Synthetic isolated project\n", encoding="utf-8")
(project_root / "GUIDE.md").write_text("# Guide\n\nSynthetic guide original.\n", encoding="utf-8")
(project_root / "README.md").write_text("# Readme\n\nSynthetic readme original.\n", encoding="utf-8")

session_id = "20261114-00000001"
session_dir = paths.data / "sessions" / session_id
session_dir.mkdir(parents=True)
current = Session(session_id, session_dir, "", SessionMeta(project_id=""), [], paths)
current.save()
run_service_main = paths.evolve / "tools" / "common" / "run_service" / "main.py"
run_service_main.parent.mkdir(parents=True, exist_ok=True)
run_service_main.write_text(
    "def run_service(payload):\n"
    "    if payload.get('action') == 'list': return {'ok': True, 'services': []}\n"
    "    return {'ok': False, 'error': 'unsupported in synthetic fixture'}\n",
    encoding="utf-8",
)
server.resume_desktop_or_create = lambda _paths: current

async def main():
    async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as websocket_server:
        port = websocket_server.sockets[0].getsockname()[1]
        if port in (5173, 5174, 8000, 8765):
            raise RuntimeError("fixture unexpectedly received a reserved development port")
        print(json.dumps({"port": port, "session_id": session_id, "root": str(root)}, separators=(",", ":")), flush=True)
        await asyncio.to_thread(sys.stdin.buffer.readline)

asyncio.run(main())
