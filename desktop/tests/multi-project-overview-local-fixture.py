"""Synthetic localhost-only multi-project overview fixture; never reads user data."""
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
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from project_switch import record_project_session
from main import WorkRecordControllerBindings
from multi_project_luna_intent import IntentCandidate
from websockets.asyncio.server import serve
import llm_client
import server

# Fail loudly if any real model path is reached. The deterministic classifier
# below is the only intent source used in this acceptance fixture.
def forbidden_model_call(*_args, **_kwargs):
    raise AssertionError("real model invocation is forbidden in this fixture")

llm_client.LLMClient.chat = forbidden_model_call

paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
for directory in (paths.workspace, paths.data, paths.evolve):
    directory.mkdir(parents=True, exist_ok=True)
(paths.evolve / "_index.core.toml").write_text("", encoding="utf-8")

current_id = "20261030-00000001"
current_dir = paths.data / "sessions" / current_id
current_dir.mkdir(parents=True)
current = Session(current_id, current_dir, "", SessionMeta(project_id=""), [], paths)
current.save()
project_sessions: dict[str, str] = {}
seed_history = "--with-history" in sys.argv[3:]
for index in range(1, 6):
    project_id = f"synthetic-project-{index}"
    session_id = f"20261030-{index + 1:08x}"
    project_root = paths.workspace / project_id
    project_root.mkdir()
    (project_root / "PROJECT.md").write_text(f"# {project_id}\n", encoding="utf-8")
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(
        session_id, session_dir, f"goal {project_id}",
        SessionMeta(active_shell="project", project_id=project_id, project_root=f"workspace/{project_id}"),
        [], paths,
    )
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(title=f"{project_id} recorded item", scope={"kind": "project", "project_id": project_id})
    requirements = [store.add_requirement(item["item_id"], f"{project_id} requirement {requirement_index}", event_id=f"{project_id}-requirement-{requirement_index}")
                    for requirement_index in range(1, 1 + (2 if index % 2 else 3))]
    if seed_history and index == 2:
        for requirement_index, requirement in enumerate(requirements[:1]):
            current_run = None
            for history_index in range(12):
                run_id = f"synthetic-history-run-{requirement_index:02d}-{history_index:02d}"
                store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                           run_id=run_id, expected_current_run_id=current_run)
                store.record_result_if_current_run(item["item_id"], requirement["requirement_id"],
                    run_id=run_id, result_id=f"synthetic-history-result-{requirement_index:02d}-{history_index:02d}", status="unknown",
                    summary=("<img src=x onerror=alert(1)>" if history_index == 0
                             else f"synthetic history row {requirement_index}-{history_index}"),
                    verification={"status": "not_verified"})
                current_run = run_id
    record_project_session(paths, project_id, session_id)
    project_sessions[project_id] = session_id

# Capture actual project sidecar reads. The file is written only in the random temp root.
read_ledger = root / "project-sidecar-reads.jsonl"
original_read = WorkRecordStore._read

def tracked_read(store):
    session_id = store._conversation_id
    if session_id in project_sessions.values():
        with read_ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"session_id": session_id}, separators=(",", ":")) + "\n")
    return original_read(store)

WorkRecordStore._read = tracked_read
run_service_main = paths.evolve / "tools" / "common" / "run_service" / "main.py"
run_service_main.parent.mkdir(parents=True, exist_ok=True)
run_service_main.write_text(
    "def run_service(payload):\n"
    "    if payload.get('action') == 'list':\n"
    "        return {'ok': True, 'services': []}\n"
    "    return {'ok': False, 'error': 'synthetic fixture supports list only'}\n",
    encoding="utf-8",
)
server.resume_desktop_or_create = lambda _paths: current

class DeterministicClassifier:
    def __call__(self, text):
        if "所有项目" in text or "全部项目" in text:
            return IntentCandidate("all")
        names = tuple(name for name in project_sessions if name in text)
        return IntentCandidate("selected", names) if names else IntentCandidate()


def isolated_controller_factory(_paths, _bridge):
    def factory(session, agent):
        controller = WorkRecordExperimentController(
            agent=agent, store=WorkRecordStore(session), recognizer=lambda *_args: {"action": "none"}
        )
        agent.multi_project_overview_recognizer = DeterministicClassifier()
        return WorkRecordControllerBindings(recall=controller)
    return factory

server._desktop_work_record_controller_factory = isolated_controller_factory

async def main():
    async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as websocket_server:
        port = websocket_server.sockets[0].getsockname()[1]
        if port in (5173, 5174, 8000, 8765):
            raise RuntimeError("fixture unexpectedly received a reserved development port")
        print(json.dumps({"port": port, "session_id": current_id, "root": str(root)}, separators=(",", ":")), flush=True)
        await asyncio.to_thread(sys.stdin.buffer.readline)

asyncio.run(main())



