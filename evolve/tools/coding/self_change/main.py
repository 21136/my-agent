"""Controlled self-change transaction evolved tool."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any


def _agent_root() -> Path:
    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        marker = directory / "evolve"
        if (marker / "_index.core.toml").is_file() or (marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def _load_core():
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from paths import AgentPaths
    from self_change import (
        SelfChangeError,
        SelfChangeState,
        SelfChangeTransaction,
        capture_baseline,
        check_candidate_health,
        load_transaction,
        prepare_branch,
        rollback_transaction,
        run_local_verification,
        save_transaction,
        switch_candidate_service,
        verify_owned_paths,
    )

    return (
        AgentPaths,
        SelfChangeError,
        SelfChangeState,
        SelfChangeTransaction,
        capture_baseline,
        check_candidate_health,
        load_transaction,
        prepare_branch,
        rollback_transaction,
        run_local_verification,
        save_transaction,
        switch_candidate_service,
        verify_owned_paths,
    )


def _resolve_working_dir(paths: Any, raw: Any) -> Path:
    text = str(raw or "").strip()
    if not text:
        return paths.agent_root.resolve()
    return paths.resolve_under_agent(text.replace("\\", "/"), must_exist=True).resolve()


def _result(tx: Any, **extra: Any) -> dict[str, Any]:
    payload = {
        "ok": True,
        "transaction_id": tx.transaction_id,
        "run_id": tx.run_id,
        "state": tx.state.value,
        "work_branch": tx.work_branch,
        "owned_paths": list(tx.owned_paths),
        "changed_paths": list(tx.changed_paths),
        "rollback_target": tx.rollback_target,
    }
    payload.update(extra)
    return payload


def run_self_change(payload: dict[str, Any], *, paths: Any | None = None) -> dict[str, Any]:
    (
        AgentPaths,
        SelfChangeError,
        SelfChangeState,
        SelfChangeTransaction,
        capture_baseline,
        check_candidate_health,
        load_transaction,
        prepare_branch,
        rollback_transaction,
        run_local_verification,
        save_transaction,
        switch_candidate_service,
        verify_owned_paths,
    ) = _load_core()
    agent_paths = paths or AgentPaths.discover(start=_agent_root())
    action = str(payload.get("action") or "").strip().lower()
    try:
        if action == "begin":
            transaction_id = str(payload.get("transaction_id") or f"self-{uuid.uuid4().hex[:12]}").strip()
            run_id = str(payload.get("run_id") or "self-change").strip()
            owned_paths = payload.get("owned_paths")
            if not isinstance(owned_paths, list) or not owned_paths:
                return {"ok": False, "error": "owned_paths must be a non-empty array"}
            root = _resolve_working_dir(agent_paths, payload.get("working_dir"))
            base_ref = str(payload.get("base_ref") or "HEAD").strip()
            snapshot = capture_baseline(root=root, owned_paths=owned_paths, base_ref=base_ref)
            branch = str(payload.get("work_branch") or f"codex/self-change/{transaction_id}").strip()
            tx = SelfChangeTransaction(
                transaction_id=transaction_id,
                run_id=run_id,
                source_root=str(root),
                target_root=str(root),
                base_ref=base_ref,
                work_branch=branch,
                owned_paths=tuple(owned_paths),
                baseline_hashes=snapshot.owned_hashes,
                changed_paths=(),
                rollback_target=base_ref,
                state=SelfChangeState.REQUESTED,
            ).advance(SelfChangeState.SCOPED).advance(SelfChangeState.SNAPSHOTTED)
            save_transaction(agent_paths, tx)
            return _result(
                tx,
                pre_existing_dirty=list(snapshot.pre_existing_dirty),
                untracked_paths=list(snapshot.untracked_paths),
                baseline_hashes=dict(snapshot.owned_hashes),
            )

        transaction_id = str(payload.get("transaction_id") or "").strip()
        if not transaction_id:
            return {"ok": False, "error": "transaction_id is required"}
        tx = load_transaction(agent_paths, transaction_id)

        if action == "status":
            return _result(tx, baseline_hashes=dict(tx.baseline_hashes), failure_reason=tx.failure_reason)
        if action == "branch":
            dry_run = bool(payload.get("dry_run", True))
            prepared = prepare_branch(tx, confirm=not dry_run)
            if prepared != tx:
                save_transaction(agent_paths, prepared)
            return _result(prepared, dry_run=dry_run)
        if action == "verify":
            working = tx
            if working.state == SelfChangeState.BRANCHED:
                working = working.advance(SelfChangeState.EDITING).advance(SelfChangeState.LOCAL_VERIFYING)
            elif working.state == SelfChangeState.EDITING:
                working = working.advance(SelfChangeState.LOCAL_VERIFYING)
            changed = verify_owned_paths(working)
            command_values = payload.get("commands") or []
            if not isinstance(command_values, list):
                return {"ok": False, "error": "commands must be an array"}
            commands = tuple(tuple(str(part) for part in command) for command in command_values)
            verification = run_local_verification(working, commands=commands) if commands else None
            if changed:
                working = working.advance(SelfChangeState.BLOCKED, changed_paths=changed, failure_reason="owned paths changed; verification is not clean")
            elif verification is not None and not verification.ok:
                working = working.advance(SelfChangeState.BLOCKED, failure_reason="local verification failed")
            elif not bool(payload.get("dry_run", True)):
                working = working.advance(SelfChangeState.CANDIDATE_READY)
            save_transaction(agent_paths, working)
            return _result(working, dry_run=bool(payload.get("dry_run", True)), verification=asdict(verification) if verification else None)
        if action == "health":
            command = payload.get("command")
            if not isinstance(command, list) or not command:
                return {"ok": False, "error": "command must be a non-empty array"}
            working = tx
            if working.state == SelfChangeState.CANDIDATE_READY:
                working = working.advance(SelfChangeState.RESTART_PENDING).advance(SelfChangeState.HEALTH_CHECKING)
            elif working.state != SelfChangeState.HEALTH_CHECKING:
                return {"ok": False, "error": f"health requires candidate_ready or health_checking, got {working.state.value}"}
            cwd = _resolve_working_dir(agent_paths, payload.get("working_dir"))
            health = check_candidate_health(
                command=tuple(str(item) for item in command),
                cwd=cwd,
                timeout_sec=float(payload.get("timeout_sec", 30)),
            )
            if health.ok:
                working = working.advance(SelfChangeState.ACCEPTED)
            else:
                working = working.advance(SelfChangeState.BLOCKED, failure_reason="candidate health check failed")
            save_transaction(agent_paths, working)
            return _result(working, health=asdict(health))
        if action == "switch":
            service_name = str(payload.get("service_name") or "").strip()
            if not service_name:
                return {"ok": False, "error": "service_name is required"}
            candidate_start = payload.get("candidate_start")
            if not isinstance(candidate_start, dict):
                candidate_start = {}
                for key in ("command", "args", "working_dir", "ready_regex", "ready_port", "ready_timeout_sec"):
                    if key in payload:
                        candidate_start[key] = payload[key]
            if not candidate_start.get("command") and not candidate_start.get("args"):
                return {"ok": False, "error": "candidate_start requires command or args"}
            service_path = _agent_root() / "evolve" / "tools" / "common" / "run_service" / "main.py"
            spec = importlib.util.spec_from_file_location("run_service_for_self_change", service_path)
            if spec is None or spec.loader is None:
                return {"ok": False, "error": "cannot load run_service"}
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            dry_run = bool(payload.get("dry_run", True))
            switched = switch_candidate_service(
                tx,
                service_name=service_name,
                candidate_start=candidate_start,
                service_runner=module.run_service,
                confirm=not dry_run,
            )
            state = str(switched.get("state") or tx.state.value)
            updated = tx
            if state == "switched" and tx.state == SelfChangeState.ACCEPTED and not dry_run:
                updated = tx.advance(SelfChangeState.SWITCHING).advance(SelfChangeState.SWITCHED)
            elif state == "rolled_back" and tx.state == SelfChangeState.ACCEPTED and not dry_run:
                updated = tx.advance(SelfChangeState.SWITCHING).advance(
                    SelfChangeState.ROLLED_BACK,
                    failure_reason="candidate service failed; old service recovered",
                )
            elif state == "blocked" and tx.state == SelfChangeState.ACCEPTED and not dry_run:
                updated = tx.advance(SelfChangeState.SWITCHING).advance(
                    SelfChangeState.BLOCKED,
                    failure_reason="candidate service switch failed",
                )
            if updated != tx:
                save_transaction(agent_paths, updated)
            return _result(updated, dry_run=dry_run, switch=switched)
        if action == "rollback":
            expected = payload.get("expected_hashes")
            if not isinstance(expected, dict):
                return {"ok": False, "error": "expected_hashes must be an object"}
            dry_run = bool(payload.get("dry_run", True))
            rolled = rollback_transaction(tx, expected_hashes={str(k): str(v) for k, v in expected.items()}, confirm=not dry_run)
            if rolled != tx:
                save_transaction(agent_paths, rolled)
            return _result(rolled, dry_run=dry_run)
        return {"ok": False, "error": "action must be begin, status, branch, verify, health, switch, or rollback"}
    except (SelfChangeError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main
    run_tool_main(run_self_change)


if __name__ == "__main__":
    main()
