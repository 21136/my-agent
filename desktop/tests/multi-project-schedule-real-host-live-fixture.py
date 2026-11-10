"""Real WebSocket + Agent + ProjectWorkerHost for multi-project schedule.dispatch live GUI E2E.

Paid live LLM (0x567-flash / gpt-6-luna) via configured ModelRegistry credentials — not mock HTTP.
Companion session stays desktop-unbound (no project_id).
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import uuid
from urllib.parse import urlsplit

core = pathlib.Path(sys.argv[1]).resolve()
sys.path[:0] = [str(core), str(core / "tests")]

import pytest
import server
from llm_client import LLMClient, load_config
from llm_models import ModelRegistry
from paths import AgentPaths
from project_mode import create_project
from project_switch import record_project_session
from project_worker_host import ProjectWorkerHost
from session import create_new
from tests.isolation_helpers import temporary_agent_paths
from websockets.asyncio.server import serve
from work_records import WorkRecordStore

COMPANION_ID = "20261109-ddddddd5"
PROJECTS = ("mp-host-alpha", "mp-host-beta")
TASKS = {
    project_id: (
        f"Reply with exactly one short sentence that includes {project_id!r} and the token SCHEDULE_LIVE_ACK. "
        "Do not call any tools."
    )
    for project_id in PROJECTS
}
SOURCE_SESSIONS = {
    "mp-host-alpha": "20261109-eeeeeee6",
    "mp-host-beta": "20261109-fffffff7",
}

WORKER_MODEL_ID = "0x567-flash"
WORKER_PROVIDER_MODEL = "gpt-6-luna"
ALLOWED_ENDPOINT = "https://api-cdn.0x567.com/v1/chat/completions"


def _configure_live_worker_credentials() -> None:
    """Resolve worker API key from the configured agent root (vault/env); never print it."""
    provider_paths = AgentPaths.discover(start=core)
    entry = ModelRegistry.load(provider_paths).get(WORKER_MODEL_ID)
    if entry is None or (entry.id, entry.vendor, entry.provider_model) != (
            WORKER_MODEL_ID, "0x567", WORKER_PROVIDER_MODEL):
        print(json.dumps({"error": "worker_model_profile_mismatch"}), flush=True)
        raise SystemExit(2)
    endpoint = entry.chat_completions_url()
    parsed = urlsplit(endpoint)
    if (endpoint != ALLOWED_ENDPOINT or parsed.scheme != "https" or parsed.hostname != "api-cdn.0x567.com"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)):
        print(json.dumps({"error": "worker_endpoint_not_allowlisted"}), flush=True)
        raise SystemExit(2)
    try:
        credential = entry.resolve_api_key(provider_paths)
    except Exception:
        print(json.dumps({"error": "credential_unavailable"}), flush=True)
        raise SystemExit(2) from None
    if not credential:
        print(json.dumps({"error": "credential_unavailable"}), flush=True)
        raise SystemExit(2)
    os.environ["OX567_API_KEY"] = credential
    del credential
    os.environ.setdefault("OX567_BASE_URL", "https://api-cdn.0x567.com")
    os.environ.setdefault("OX567_MODEL_FLASH", WORKER_PROVIDER_MODEL)
    timeout = os.environ.get("REAL_MULTI_PROJECT_SCHEDULE_LIVE_TIMEOUT_SEC", "120")
    os.environ.setdefault("LLM_TIMEOUT_SEC", timeout)


def _posix_test_publish_parent_by_handle(namespace, raw: bytes) -> None:
    target = namespace._path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(raw)
    temporary.replace(target)


async def main() -> None:
    if os.environ.get("REAL_MULTI_PROJECT_SCHEDULE_LIVE_OPT_IN") != "1":
        print(json.dumps({"error": "live_opt_in_required"}), flush=True)
        raise SystemExit(2)
    _configure_live_worker_credentials()
    if os.name != "nt":
        import multi_project_work_record_bindings as mpb
        mpb._publish_parent_by_handle = _posix_test_publish_parent_by_handle
    with temporary_agent_paths(copy_tool_dirs=("common/interactive_terminal", "common/run_service")) as paths:
        for project_id in PROJECTS:
            create_project(paths, project_id, project_template="light")
            source = create_new(paths, conversation_id=SOURCE_SESSIONS[project_id])
            source.meta.project_id = project_id
            source.meta.project_root = f"workspace/{project_id}"
            source.meta.harness = "desktop"
            source.meta.turn_mode = "agent"
            source.save()
            store = WorkRecordStore(source)
            item = store.create_item(
                title=f"Schedule item {project_id}",
                scope={"kind": "project", "project_id": project_id},
            )
            store.add_requirement(item["item_id"], TASKS[project_id], event_id=f"req-{project_id}")
            record_project_session(paths, project_id, source.conversation_id)

        companion = create_new(paths, conversation_id=COMPANION_ID)
        companion.meta.harness = "desktop"
        companion.meta.turn_mode = "agent"
        companion.meta.active_shell = "grow"
        companion.save()
        assert not (companion.meta.project_id or "").strip()

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(server, "resume_desktop_or_create", lambda _paths: companion)
        real_build = server._build_repl

        def build_repl(bound_session, bound_paths, bridge, **kwargs):
            repl = real_build(bound_session, bound_paths, bridge, **kwargs)
            host = ProjectWorkerHost(lambda: LLMClient(load_config(), paths=bound_paths))
            real_run_many = host.run_many

            def bounded_run_many(*args, **kwargs):
                kwargs["max_requests"] = min(int(kwargs.get("max_requests", 5)), 2)
                return real_run_many(*args, **kwargs)

            host.run_many = bounded_run_many
            repl.agent.project_worker_host = host
            return repl

        monkeypatch.setattr(server, "_build_repl", build_repl)
        monkeypatch.setattr(server, "emit_corruption_notices", lambda *_a: None)
        monkeypatch.setattr(server, "_emit_pending_work_card_references", lambda *_a: None)

        ready = {
            "port": 0,
            "companion_session_id": COMPANION_ID,
            "project_ids": list(PROJECTS),
            "source_sessions": dict(SOURCE_SESSIONS),
            "synthetic_model_transport": False,
            "live_paid_llm": True,
            "worker_model_id": WORKER_MODEL_ID,
            "real_project_worker_host": True,
        }

        async with serve(server.WsSessionHandler(paths).handle, "127.0.0.1", 0) as ws:
            ready["port"] = ws.sockets[0].getsockname()[1]
            print(json.dumps(ready), flush=True)
            await asyncio.to_thread(sys.stdin.buffer.readline)
        monkeypatch.undo()


if __name__ == "__main__":
    asyncio.run(main())
