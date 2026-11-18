from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from self_evolution_types import CandidateBinding, EvolutionIdentity, EvolutionSnapshot
from self_evolution_store import StoreConflict, _snapshot_from, _snapshot_dict, EvolutionStore
from self_evolution_host import (HostRunContext, SelfEvolutionHost, SelfEvolutionHostConfig,
                                 TrustedWorkerCapabilities)
from self_evolution_worker import HostTool
from self_evolution_types import EvolutionRequest
from self_evolution_verification import WorkspaceBinding

_TESTS = str(Path(__file__).parent)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
from test_self_evolution_materializer import _IsolatedTestMaterializer


def snapshot_candidate(**overrides):
    values = dict(
        candidate_id="task-A", stable_commit="a" * 40,
        git_common_id=None, stable_worktree_id=None, candidate_worktree_id=None,
        baseline_manifest_digest="b" * 64, candidate_kind="materialized_snapshot",
        snapshot_root=os.path.abspath("snapshot-root"), snapshot_tree_oid="c" * 40,
        snapshot_binding_digest="d" * 64,
    )
    values.update(overrides)
    return CandidateBinding(**values)


def test_snapshot_binding_is_discriminated_and_contains_no_fake_git_identity():
    binding = snapshot_candidate()
    assert binding.candidate_kind == "materialized_snapshot"
    assert binding.git_common_id is None
    assert binding.stable_worktree_id is None
    assert binding.candidate_worktree_id is None
    assert binding.snapshot_tree_oid == "c" * 40
    with pytest.raises(ValueError):
        snapshot_candidate(candidate_worktree_id="invented")
    with pytest.raises(ValueError):
        snapshot_candidate(snapshot_binding_digest="not-a-digest")


def test_legacy_candidate_payload_defaults_to_git_worktree():
    legacy = {
        "candidate_id": "candidate-A", "stable_commit": "a" * 40,
        "git_common_id": "common-A", "stable_worktree_id": "stable-A",
        "candidate_worktree_id": "candidate-wt-A", "baseline_manifest_digest": "b" * 64,
    }
    identity = EvolutionIdentity("session-A", "task-A", "attempt-A", "run-A", 4)
    decoded = _snapshot_from({
        "identity": {"session_id": identity.session_id, "task_id": identity.task_id,
                     "attempt_id": identity.attempt_id, "run_id": identity.run_id,
                     "turn_epoch": identity.turn_epoch},
        "phase": "preparing", "snapshot_revision": 1, "candidate": legacy,
        "candidate_digest": None, "discoveries": [], "change_summary": None,
        "evidence": [], "blocker_code": None, "error_code": None,
        "allowed_next_actions": [],
    })
    assert decoded.candidate.candidate_kind == "git_worktree"


def test_store_round_trips_snapshot_binding_and_rejects_task_mismatch(tmp_path: Path):
    path = tmp_path / "snapshot-store.sqlite3"
    options = {}
    if os.name == "nt":
        temp_root = Path(tempfile.gettempdir()).resolve()
        options["trusted_acl_verifier"] = lambda item: item.resolve().is_relative_to(temp_root)
    db = EvolutionStore(path, **options)
    identity = EvolutionIdentity("session-A", "task-A", "attempt-A", "run-A", 4)
    binding = snapshot_candidate()
    snapshot = EvolutionSnapshot(identity, "preparing", 1, candidate=binding)
    db.create_task(identity, snapshot)
    restored = db.read_task("session-A", "task-A")
    assert restored == snapshot
    assert _snapshot_dict(restored)["candidate"]["candidate_kind"] == "materialized_snapshot"

    bad = snapshot_candidate(candidate_id="other-task")
    with pytest.raises(StoreConflict):
        db.create_task(identity, EvolutionSnapshot(identity, "preparing", 1, candidate=bad))


class _RunContext:
    def __init__(self):
        self.value = HostRunContext(
            conversation_id="snapshot-session", run_id="snapshot-run", turn_epoch=8,
            mode="agent", work_intent="execute", cancel_event=threading.Event(),
            request_id="00000000-0000-4000-8000-000000000811",
            request_origin="human", active=True, self_evolution_intent=True,
        )

    def __call__(self):
        return self.value


class _WorkerResponse:
    def __init__(self, *, content=None, tool_call=None):
        self.content = content
        self.tool_calls = [tool_call] if tool_call else []
        self.finish_reason = "tool_calls" if tool_call else "stop"
        self.raw = {"choices": [{"message": {"tool_calls": self.tool_calls}}]}


def _tool_call(name, arguments):
    return {"id": "call-" + name, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }}


class _ObserveOnceClient:
    def __init__(self):
        self.calls = 0
        self._cancel_event = None
        self.tool_schemas = ()

    def set_cancel_event(self, event):
        self._cancel_event = event

    def chat_confirmed_tools(self, _messages, **kwargs):
        self.calls += 1
        self.tool_schemas = kwargs["tools"]
        kwargs["on_request_attempt"]()
        if kwargs["after_each"]() is not True:
            raise RuntimeError("host binding rejected")
        if self.calls == 1:
            return _WorkerResponse(tool_call=_tool_call("observe_candidate", {}))
        raise RuntimeError("intentional fixture provider failure after binding/tool check")


class _ControlledSnapshotHost(SelfEvolutionHost):
    @property
    def production_ready(self):
        return True

    @property
    def full_candidate_coverage_available(self):
        return True

    @property
    def snapshot_candidate_creation_available(self):
        return True


def test_controlled_snapshot_start_persists_discriminated_binding_and_recovers(tmp_path: Path):
    from self_evolution_host import SelfEvolutionHost as ProductionHost
    from self_evolution_store import StartRequestClaim
    from self_evolution_workspace import WorkspaceError
    from test_self_evolution_host import _test_store
    import self_evolution_workspace

    stable = tmp_path / "stable"
    trust = tmp_path / "candidate-root"
    stable.mkdir(); trust.mkdir()
    subprocess.run(["git", "init", "--quiet", str(stable)], check=True)
    subprocess.run(["git", "-C", str(stable), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(stable), "config", "user.email", "fixture@example.invalid"], check=True)
    # Keep the committed blob identical to the working file on Windows so the
    # attribution preflight is not testing Git's host-specific CRLF setting.
    subprocess.run(["git", "-C", str(stable), "config", "core.autocrlf", "false"], check=True)
    (stable / "tracked.txt").write_text("committed\n", encoding="utf-8")
    (stable / "fixture-video.mp4").write_bytes(b"snapshot-video")
    subprocess.run(["git", "-C", str(stable), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(stable), "commit", "--quiet", "-m", "fixture"], check=True)
    context = _RunContext()
    config = SelfEvolutionHostConfig(
        stable_repository=stable, trust_root=trust, attribution_paths=("tracked.txt",),
        verification_source_paths=("tracked.txt",),
        verification_commands={"syntax": (sys.executable, "-c", "print('ok')")},
        verification_timeout_seconds=5,
    )
    store = _test_store(tmp_path / "evolution.sqlite3")
    client = _ObserveOnceClient()
    capabilities = TrustedWorkerCapabilities(
        client=client, model_id="fixture-model",
        outbound_request_confirm_fn=lambda _preview, _allow: "y",
        tools=(HostTool("observe_candidate", "test-only", lambda _ctx, _args: {}),),
        process_tree_stopped=lambda _identity: True, enable_experimental_start=True,
    )
    host = _ControlledSnapshotHost(
        config, integrity_key=b"controlled-snapshot-host-key-32bytes",
        context_provider=context, trusted_verifier_resolver=lambda task, record, cfg, cid:
            WorkspaceBinding(task, record["candidate_path"], cid, cfg, config.verification_source_paths),
        store=store, worker_capabilities=capabilities,
    )
    # Real production binding format and read path; only the cleanup safety
    # gate is replaced for this disposable test repository.
    git_executable = shutil.which("git")
    assert git_executable is not None
    host._snapshot_materializer = _IsolatedTestMaterializer(git_executable)
    # Test-only candidate digest stands in for the still-closed production
    # full-tree gate; binding checks and real file tools remain active.
    host._current_candidate_digest = lambda _state: "f" * 64
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(self_evolution_workspace, "SafeSnapshotMaterializer", _IsolatedTestMaterializer)
        claim = store.reserve_start_request(
            context.value.conversation_id, context.value.request_id, "e" * 64,
            run_id=context.value.run_id, turn_epoch=context.value.turn_epoch,
        )
        assert type(claim) is StartRequestClaim
        started = host.start(EvolutionRequest("Observe the fixture"), start_request_claim=claim)
    assert started["status"] == "started", started
    store.finish_start_request(claim, {
        "status": "started", "task_id": started["task_id"], "attempt_id": started["attempt_id"],
        "run_id": started["run_id"], "worker_bound": True, "os_sandbox": False,
        "merge_authorized": False,
    })
    assert host.wait_for_worker(started["task_id"], started["attempt_id"], timeout=10)
    initial = store.read_task(context.value.conversation_id, started["task_id"])
    assert initial is not None
    assert initial.phase == "failed", {"phase": initial.phase, "blocker": initial.blocker_code,
        "error": initial.error_code, "evidence": initial.evidence}
    assert initial.identity.attempt_id == started["attempt_id"]
    assert initial.candidate.candidate_kind == "materialized_snapshot"
    assert initial.candidate.git_common_id is None
    assert initial.candidate.snapshot_root == str(trust / started["task_id"])
    assert {item["function"]["name"] for item in client.tool_schemas} >= {
        "observe_candidate", "read_candidate_file", "write_candidate_file", "run_registered_check",
    }
    assert all("root" not in item["function"]["parameters"].get("properties", {})
               for item in client.tool_schemas)

    # A normal production Host remains gated; the subclass above is test-only.
    production = ProductionHost(
        config, integrity_key=b"controlled-snapshot-host-key-32bytes",
        context_provider=context, trusted_verifier_resolver=lambda *_args: None, store=store,
    )
    assert production.full_candidate_coverage_available is False
    assert production.snapshot_candidate_creation_available is False
    restored = production.get_snapshot(started["task_id"], session_id=context.value.conversation_id)
    assert restored is not None and restored.identity == initial.identity, {
        "startup_error": production._startup_reconciliation_error,
        "row": store.read_task(context.value.conversation_id, started["task_id"]),
        "local_match": production._tasks.get(started["task_id"]),
    }
    assert production.get_snapshot(started["task_id"], session_id="foreign-session") is None
    # Recovery is status-only: process-sealed review baselines are not recreated.
    assert not production._tasks

    # Moving the original candidate inode beneath a replacement trust root must
    # fail for both this Host and a newly constructed Host. Copying only a path
    # or retaining the candidate directory identity cannot authenticate its parent.
    candidate_path = trust / started["task_id"]
    displaced = tmp_path / "original-candidate-root"
    trust.rename(displaced)
    try:
        trust.mkdir()
        (displaced / started["task_id"]).rename(candidate_path)
        try:
            assert production.get_snapshot(started["task_id"]) is None
            with pytest.raises(WorkspaceError) as mismatch:
                production._inspect_snapshot_record(started["task_id"])
            assert mismatch.value.code == "binding_identity_mismatch"
            restarted = ProductionHost(
                config, integrity_key=b"controlled-snapshot-host-key-32bytes",
                context_provider=context, trusted_verifier_resolver=lambda *_args: None, store=store,
            )
            assert restarted.get_snapshot(started["task_id"]) is None
            with pytest.raises(WorkspaceError) as restart_mismatch:
                restarted._inspect_snapshot_record(started["task_id"])
            assert restart_mismatch.value.code == "binding_identity_mismatch"
        finally:
            candidate_path.rename(displaced / started["task_id"])
            trust.rmdir()
    finally:
        displaced.rename(trust)
    assert production.get_snapshot(started["task_id"]) == restored

    wrong_key_host = ProductionHost(
        config, integrity_key=b"different-test-integrity-key-32bytes",
        context_provider=context, trusted_verifier_resolver=lambda *_args: None, store=store,
    )
    assert wrong_key_host.get_snapshot(started["task_id"]) is None
    with pytest.raises(WorkspaceError) as wrong_key:
        wrong_key_host._inspect_snapshot_record(started["task_id"])
    assert wrong_key.value.code == "binding_tampered"
    binding_path = candidate_path / ".self-evolution-binding.json"
    original_binding = binding_path.read_bytes()
    try:
        envelope = json.loads(original_binding)
        envelope["record"]["manifest_digest"] = "0" * 64
        binding_path.write_text(json.dumps(envelope), encoding="utf-8")
        assert production.get_snapshot(started["task_id"]) is None
    finally:
        binding_path.write_bytes(original_binding)
    assert production.get_snapshot(started["task_id"]) == restored

    # A persisted snapshot binding that no longer matches the authenticated
    # materializer record is rejected during restore rather than trusted.
    with store._connection() as db:
        row = db.execute("SELECT payload FROM tasks WHERE task_id=?", (started["task_id"],)).fetchone()
        payload = json.loads(row["payload"])
        payload["candidate"]["snapshot_binding_digest"] = "0" * 64
        db.execute("UPDATE tasks SET payload=? WHERE task_id=?",
                   (json.dumps(payload, sort_keys=True, separators=(",", ":")), started["task_id"]))
    assert production.get_snapshot(started["task_id"]) is None
