from __future__ import annotations

import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_host import (  # noqa: E402
    HostRunContext,
    SelfEvolutionHost,
    SelfEvolutionHostConfig,
    TrustedWorkerCapabilities,
    HostTool,
)
from self_evolution_store import EvolutionStore, StoreOwnershipError  # noqa: E402
from self_evolution_types import EvolutionIdentity, EvolutionRequest, EvolutionSnapshot  # noqa: E402


def _store(path: Path) -> EvolutionStore:
    options = {}
    if os.name == "nt":
        import tempfile
        root = Path(tempfile.gettempdir()).resolve()
        options["trusted_acl_verifier"] = lambda target: target.resolve().is_relative_to(root)
    return EvolutionStore(path, **options)


def _identity(task_id: str) -> EvolutionIdentity:
    return EvolutionIdentity("session-A", task_id, f"attempt-{task_id}", "run-A", 3)


class _Client:
    def chat_confirmed_tools(self, *_args, **_kwargs):
        raise AssertionError("worker must not be started in this test")

    def set_cancel_event(self, _event):
        pass

class _TestHost(SelfEvolutionHost):
    @property
    def full_candidate_coverage_available(self):
        return True

def _host(store: EvolutionStore, context: HostRunContext, candidate_create) -> SelfEvolutionHost:
    config = SelfEvolutionHostConfig(
        stable_repository=Path("unused-stable"), trust_root=Path("unused-candidates"),
        attribution_paths=("README.md",), verification_source_paths=("README.md",),
        verification_commands={"check": ("python", "-c", "pass")},
    )
    capabilities = TrustedWorkerCapabilities(
        client=_Client(), model_id="test", outbound_request_confirm_fn=lambda *_: "n",
        tools=(HostTool("observe_candidate", "observe", lambda *_: {"status": "unavailable"}),), process_tree_stopped=lambda _identity: False,
        enable_experimental_start=True,
    )
    host = _TestHost(
        config, integrity_key=b"x" * 32, context_provider=lambda: context,
        trusted_verifier_resolver=lambda *_: None, store=store,
        worker_capabilities=capabilities,
    )
    host._workspace.create = candidate_create
    return host


def _context(*, run_id: str = "run-A") -> HostRunContext:
    return HostRunContext(
        conversation_id="session-A", run_id=run_id, turn_epoch=3, mode="agent",
        work_intent="execute", cancel_event=threading.Event(),
        request_id="00000000-0000-4000-8000-000000000001",
        request_origin="human", active=True, self_evolution_intent=True,
    )


def test_host_rejects_store_claim_bound_to_another_run_before_candidate_creation(tmp_path):
    store = _store(tmp_path / "start.sqlite3")
    candidate_calls = []
    context = _context()
    host = _host(store, context, lambda: candidate_calls.append("create"))
    claim = store.reserve_start_request(
        "session-A", context.request_id, "a" * 64, run_id="run-other", turn_epoch=3,
    )

    result = host.start(EvolutionRequest("goal"), start_request_claim=claim)

    assert result["status"] == "denied"
    assert result["error_code"] == "persistent_start_claim_rejected"
    assert candidate_calls == []

def test_consumed_claim_is_durably_bound_to_exactly_one_task(tmp_path):
    store = _store(tmp_path / "task-binding.sqlite3")
    request_id = str(uuid.uuid4())
    claim = store.reserve_start_request(
        "session-A", request_id, "b" * 64, run_id="run-A", turn_epoch=3,
    )
    store.consume_start_request_claim(claim, session_id="session-A", run_id="run-A", turn_epoch=3)

    first = _identity("task-first")
    store.create_task_with_writer(first, EvolutionSnapshot(first, "preparing", 1), "owner-first",
                                  start_request_claim=claim)
    store.release_writer("owner-first", stop_confirmed=True)
    store.compare_and_swap(first.task_id, 1, EvolutionSnapshot(first, "failed", 2))

    second = _identity("task-second")
    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(second, EvolutionSnapshot(second, "preparing", 1), "owner-second",
                                      start_request_claim=claim)

    assert store.read_task("session-A", second.task_id) is None













def test_unmapped_legacy_task_for_same_run_epoch_blocks_claim_rebinding(tmp_path):
    path = tmp_path / "legacy-task-binding.sqlite3"
    store = _store(path)
    claim = store.reserve_start_request(
        "session-A", str(uuid.uuid4()), "c" * 64, run_id="run-A", turn_epoch=3,
    )
    store.consume_start_request_claim(claim, session_id="session-A", run_id="run-A", turn_epoch=3)
    legacy = _identity("legacy-task")
    store.create_task_with_writer(legacy, EvolutionSnapshot(legacy, "preparing", 1), "legacy-owner",
                                  start_request_claim=claim)
    store.release_writer("legacy-owner", stop_confirmed=True)
    store.compare_and_swap(legacy.task_id, 1, EvolutionSnapshot(legacy, "failed", 2))
    # Simulate a task committed by the pre-task-binding schema. The production
    # migration adds an empty association table and cannot infer request identity.
    with store._connection() as db:
        db.execute("DELETE FROM start_request_tasks WHERE session_id=? AND request_id=?",
                   (claim.session_id, claim.request_id))

    second = _identity("second-task")
    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(second, EvolutionSnapshot(second, "preparing", 1), "second-owner",
                                      start_request_claim=claim)
    assert store.read_task("session-A", second.task_id) is None


def test_new_request_in_same_run_epoch_can_follow_bound_terminal_task(tmp_path):
    store = _store(tmp_path / "bound-terminal.sqlite3")
    first_claim = store.reserve_start_request(
        "session-A", str(uuid.uuid4()), "d" * 64, run_id="run-A", turn_epoch=3,
    )
    store.consume_start_request_claim(first_claim, session_id="session-A", run_id="run-A", turn_epoch=3)
    first = _identity("prior-bound-task")
    store.create_task_with_writer(first, EvolutionSnapshot(first, "preparing", 1), "first-owner",
                                  start_request_claim=first_claim)
    store.finish_start_request(first_claim, {"status": "started", "task_id": first.task_id})
    store.release_writer("first-owner", stop_confirmed=True)
    store.compare_and_swap(first.task_id, 1, EvolutionSnapshot(first, "failed", 2))

    next_claim = store.reserve_start_request(
        "session-A", str(uuid.uuid4()), "e" * 64, run_id="run-A", turn_epoch=3,
    )
    assert next_claim.status == "claimed"
    store.consume_start_request_claim(next_claim, session_id="session-A", run_id="run-A", turn_epoch=3)
    second = _identity("new-bound-task")
    store.create_task_with_writer(second, EvolutionSnapshot(second, "preparing", 1), "second-owner",
                                  start_request_claim=next_claim)
    assert store.read_task("session-A", second.task_id).identity == second
    with store._connection() as db:
        rows = db.execute("SELECT request_id, task_id FROM start_request_tasks ORDER BY task_id").fetchall()
    assert {(row["request_id"], row["task_id"]) for row in rows} == {
        (first_claim.request_id, first.task_id), (next_claim.request_id, second.task_id),
    }


def test_mixed_bound_and_unbound_terminal_tasks_still_block_new_request(tmp_path):
    store = _store(tmp_path / "mixed-legacy.sqlite3")
    first_claim = store.reserve_start_request(
        "session-A", str(uuid.uuid4()), "f" * 64, run_id="run-A", turn_epoch=3,
    )
    store.consume_start_request_claim(first_claim, session_id="session-A", run_id="run-A", turn_epoch=3)
    first = _identity("prior-bound-task")
    store.create_task_with_writer(first, EvolutionSnapshot(first, "preparing", 1), "first-owner",
                                  start_request_claim=first_claim)
    store.finish_start_request(first_claim, {"status": "started", "task_id": first.task_id})
    store.release_writer("first-owner", stop_confirmed=True)
    store.compare_and_swap(first.task_id, 1, EvolutionSnapshot(first, "failed", 2))
    legacy = _identity("legacy-unbound-task")
    store.create_task(legacy, EvolutionSnapshot(legacy, "preparing", 1))
    store.compare_and_swap(legacy.task_id, 1, EvolutionSnapshot(legacy, "failed", 2))

    next_claim = store.reserve_start_request(
        "session-A", str(uuid.uuid4()), "0" * 64, run_id="run-A", turn_epoch=3,
    )
    store.consume_start_request_claim(next_claim, session_id="session-A", run_id="run-A", turn_epoch=3)
    second = _identity("new-task")
    with pytest.raises(StoreOwnershipError):
        store.create_task_with_writer(second, EvolutionSnapshot(second, "preparing", 1), "second-owner",
                                      start_request_claim=next_claim)
    assert store.read_task("session-A", second.task_id) is None
