from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_host import (  # noqa: E402
    HostRunContext,
    TrustedHumanMessageBinding,
    _TaskState,

    SelfEvolutionHost,
    SelfEvolutionHostConfig,
)
from self_evolution_verification import WorkspaceBinding  # noqa: E402
from self_evolution_worker import HostTool, SelfEvolutionWorkerResult
from self_evolution_store import EvolutionStore, StoreOwnershipError
from self_evolution_types import EvolutionEvent, EvolutionIdentity, EvolutionSnapshot, EvolutionRequest, VerificationEvidence
from self_evolution_projection import project_evolution
from self_evolution_guidance import GuidanceInbox


def _test_store(path):
    """Temporary-root-only ACL test double; does not enable candidate checkout."""
    options = {}
    if os.name == "nt":
        target = Path(path)
        directory = target.parent if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"} else target
        directory.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.gettempdir()).resolve()
        options["trusted_acl_verifier"] = lambda item: item.resolve().is_relative_to(temp_root)
    return EvolutionStore(path, **options)


@dataclass
class _LiveContext:
    conversation_id: str = "conversation-1"
    run_id: str = "run-1"
    turn_epoch: int = 7
    mode: str = "agent"
    work_intent: str = "execute"
    cancel_event: threading.Event | None = None
    request_id: str = "00000000-0000-4000-8000-000000000001"
    request_origin: str = "human"
    active: bool = True
    self_evolution_intent: bool = True

    def snapshot(self):
        return HostRunContext(
            conversation_id=self.conversation_id,
            run_id=self.run_id,
            turn_epoch=self.turn_epoch,
            mode=self.mode,
            work_intent=self.work_intent,
            cancel_event=self.cancel_event or threading.Event(),
            request_id=self.request_id,
            request_origin=self.request_origin,
            active=self.active,
            self_evolution_intent=self.self_evolution_intent,
        )



class _WorkerClient:
    def __init__(self):
        self.calls = 0
        self._active_client = None
        self._cancel_event = None

    def set_cancel_event(self, event):
        self._cancel_event = event

    def chat_confirmed_tools(self, messages, **kwargs):
        self.calls += 1
        kwargs["on_request_attempt"]()
        if kwargs["confirm_fn"]("frozen preview", False) != "y" or kwargs["after_each"]() is not True:
            raise RuntimeError("not authorized")
        if self.calls == 1:
            return _Response(calls=[_tool_call("observe_candidate", {})], finish="tool_calls")
        if self.calls == 2:
            return _Response(calls=[_tool_call("write_candidate_file", {"path": "tracked.txt", "content": "candidate\
"})], finish="tool_calls")
        if self.calls == 3:
            return _Response(calls=[_tool_call("run_registered_check", {"check_id": "syntax"})], finish="tool_calls")
        return _Response(content="Completed the requested change.")


class _BlockingWorkerClient(_WorkerClient):
    def __init__(self, entered, release):
        super().__init__()
        self.entered = entered
        self.release = release

    def chat_confirmed_tools(self, messages, **kwargs):
        self.entered.set()
        self.release.wait(2)
        return super().chat_confirmed_tools(messages, **kwargs)


def _tool_call(name, arguments):
    import json
    return {"id": f"call-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class _Response:
    def __init__(self, *, content=None, calls=None, finish="stop"):
        self.content = content
        self.tool_calls = calls or []
        self.finish_reason = finish
        self.raw = {"choices": [{"message": {"tool_calls": self.tool_calls}}]}


def _worker_capabilities(client, *, stopped, tools=None):
    from self_evolution_host import TrustedWorkerCapabilities
    return TrustedWorkerCapabilities(client=client, model_id="trusted-model",
                                      outbound_request_confirm_fn=lambda _preview, _allow_all: "y",
                                      tools=tools or _worker_tools(),
                                      process_tree_stopped=lambda _identity: stopped,
                                      enable_experimental_start=True)

def _worker_tools():
    return (
        HostTool("observe_candidate", "observe", lambda _context, _args: {"status": "observed", "evidence_ref": "observation-1"}),
        HostTool("read_candidate_file", "read", lambda _context, _args: {"status": "ok", "content": "source"}),
        HostTool("write_candidate_file", "write", lambda _context, _args: {"status": "written", "evidence_ref": "write-1"}),
        HostTool("run_registered_check", "check", lambda context, args: {"status": "passed", "check_id": args["check_id"], "candidate_digest": context.candidate_digest, "evidence_ref": "private://check/1", "exit_code": 0, "process_tree_state": "stopped",
                                  "requires_reconciliation": False,
                                  "evidence_scope": "declared_source_paths_and_windows_job_tree"}),
    )

class _IsolatedTestWorkspace:
    """Materialize independent temporary clones; not the production worktree path."""
    def __init__(self, stable, trust_root, integrity_key):
        import hashlib
        import hmac
        import json
        import uuid

        self.stable = Path(stable)
        self.trust_root = Path(trust_root)
        self.key = integrity_key
        self.records = {}
        self._hashlib = hashlib
        self._hmac = hmac
        self._json = json
        self._uuid = uuid

    @staticmethod
    def _git(path, *args):
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError("isolated test git operation failed")
        return result.stdout.strip()

    def _identity(self, path):
        return {
            "root": str(Path(self._git(path, "rev-parse", "--show-toplevel")).resolve()),
            "git_common_dir": str(Path(self._git(path, "rev-parse", "--git-common-dir")).resolve()),
            "git_dir": str(Path(self._git(path, "rev-parse", "--absolute-git-dir")).resolve()),
        }

    def create(self):
        task_id = str(self._uuid.uuid4())
        candidate = self.trust_root / task_id
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", "--no-local",
                        str(self.stable), str(candidate)], check=True, capture_output=True)
        stable_head = self._git(self.stable, "rev-parse", "--verify", "HEAD^{commit}")
        candidate_head = self._git(candidate, "rev-parse", "--verify", "HEAD^{commit}")
        record = {
            "schema": "self-evolution-workspace.v2", "task_id": task_id, "status": "created",
            "stable_repository": self._identity(self.stable), "stable_head": stable_head,
            "candidate_repository": self._identity(candidate), "candidate_head": candidate_head,
            "candidate_path": str(candidate), "object_store": "isolated_test_copy",
            "history_depth": None, "stable_worktree_content_verified": False, "os_sandbox": False,
        }
        record["baseline_manifest_digest"] = self._hashlib.sha256(
            self._json.dumps({key: record[key] for key in ("task_id", "stable_repository", "stable_head",
                                                           "candidate_repository", "candidate_head")},
                             sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        canonical = self._json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        envelope = {"record": record, "hmac_sha256": self._hmac.new(self.key, canonical, self._hashlib.sha256).hexdigest()}
        path = self.trust_root / f"{task_id}.self-evolution-workspace.json"
        path.write_text(self._json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        self.records[task_id] = record
        return record

    def inspect(self, task_id):
        import hmac
        import json
        from self_evolution_workspace import WorkspaceError

        path = self.trust_root / f"{task_id}.self-evolution-workspace.json"
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            record = envelope["record"]
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            expected = hmac.new(self.key, canonical, self._hashlib.sha256).hexdigest()
            if not hmac.compare_digest(envelope.get("hmac_sha256", ""), expected):
                raise WorkspaceError("Test manifest integrity failed.", code="manifest_tampered", stage="inspect")
            if record != self.records[task_id] or record.get("task_id") != task_id:
                raise WorkspaceError("Test manifest identity changed.", code="identity_mismatch", stage="inspect")
            return record
        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceError("Test manifest unavailable.", code="manifest_invalid", stage="inspect") from exc


class _IsolatedTestHost(SelfEvolutionHost):
    def __init__(self, *args, test_full_candidate_coverage=False, **kwargs):
        self._test_full_candidate_coverage = test_full_candidate_coverage
        super().__init__(*args, **kwargs)

    @property
    def production_ready(self):
        # Only this test subclass opts into host orchestration assertions.
        return True

    @property
    def full_candidate_coverage_available(self):
        # Enabled only for isolated tests using the disposable full-repository clone.
        return self._test_full_candidate_coverage


class SelfEvolutionHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="self-evolution-host-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stable = self.root / "stable"
        self.trust_root = self.root / "candidates"
        self.stable.mkdir()
        self.trust_root.mkdir()
        self.git("init", "--quiet", str(self.stable))
        self.git("-C", str(self.stable), "config", "user.name", "Fixture")
        self.git("-C", str(self.stable), "config", "user.email", "fixture@example.invalid")
        self.git("-C", str(self.stable), "config", "core.autocrlf", "false")
        (self.stable / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", "tracked.txt")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "initial")
        self.live = _LiveContext()
        self.config = SelfEvolutionHostConfig(
            stable_repository=self.stable,
            trust_root=self.trust_root,
            attribution_paths=("tracked.txt",),
            verification_source_paths=("tracked.txt",),
            verification_commands={"syntax": (sys.executable, "-c", "print('trusted-check')")},
            verification_timeout_seconds=3,
        )
        self.host = self.make_host()

    def test_worker_guidance_receive_fails_closed_without_trusted_raw_message_resolver(self) -> None:
        with patch.object(self.host, "_live_context", return_value=(replace(self.live.snapshot(), request_origin="worker"), None)):
            receipt = self.host.receive_worker_guidance("task", "attempt")
        self.assertEqual(receipt["status"], "denied")
        self.assertEqual(receipt["reason"], "human_request_required")

    def test_worker_guidance_never_accepts_caller_body_without_trusted_binding(self) -> None:
        receipt = self.host.receive_worker_guidance("task", "attempt")
        self.assertEqual(receipt["status"], "denied")
        self.assertEqual(receipt["reason"], "human_message_binding_unavailable")
        with self.assertRaises(TypeError):
            self.host.receive_worker_guidance("task", "attempt", "model-forged text")

    def test_worker_guidance_receive_is_attempt_bound_and_only_reports_pending(self) -> None:
        state = _TaskState("task-1", (), {}, None, {})
        state.identity = EvolutionIdentity("conversation-1", "task-1", "attempt-1", "old-run", 2)
        state.cancel_event = threading.Event()
        state.guidance_inbox = GuidanceInbox(("conversation-1", "task-1", "attempt-1"))
        binding_text = "actual raw human message"
        binding = [TrustedHumanMessageBinding(
            context_identity=self.live.snapshot().identity(), request_id="00000000-0000-4000-8000-000000000099",
            message_id="other-turn-message", raw_text="other turn text",
        )]
        self.host = self.make_host(human_message_resolver=lambda _ctx: binding[0])
        self.host._tasks["task-1"] = state
        with patch.object(self.host, "_worker_binding_valid", return_value=True):
            mismatch = self.host.receive_worker_guidance("task-1", "other-attempt")
            wrong_request = self.host.receive_worker_guidance("task-1", "attempt-1")
        binding[0] = TrustedHumanMessageBinding(
            context_identity=self.live.snapshot().identity(), request_id=self.live.request_id,
            message_id="trusted-message-1", raw_text=binding_text,
        )
        with patch.object(self.host, "_worker_binding_valid", return_value=True):
            accepted = self.host.receive_worker_guidance("task-1", "attempt-1")
        self.assertEqual(mismatch["status"], "denied")
        self.assertEqual(wrong_request["status"], "denied")
        self.assertEqual(accepted, {"status": "received_pending_delivery", "message_id": "trusted-message-1", "delivery": "pending"})
        self.assertEqual(state.guidance_inbox.snapshot()[0].text, binding_text)
        status = self.host.get_worker_guidance_status("task-1", "attempt-1")
        self.assertEqual(status, {"status": "ok", "messages": [{"message_id": "trusted-message-1", "delivery": "received_pending_delivery"}]})

    def test_worker_guidance_rechecks_live_request_identity_after_waiting_for_task_lock(self) -> None:
        state = _TaskState("task-1", (), {}, None, {})
        state.identity = EvolutionIdentity("conversation-1", "task-1", "attempt-1", "old-run", 2)
        state.cancel_event = threading.Event()
        state.guidance_inbox = GuidanceInbox(("conversation-1", "task-1", "attempt-1"))
        self.host = self.make_host(human_message_resolver=lambda ctx: TrustedHumanMessageBinding(
            context_identity=ctx.identity(), request_id=ctx.request_id,
            message_id="trusted-message-1", raw_text="actual user text",
        ))
        self.host._tasks["task-1"] = state
        captured = threading.Event()
        original_provider = self.host._context_provider

        def provider():
            context = original_provider()
            captured.set()
            return context

        self.host._context_provider = provider
        result = []
        state.lock.acquire()
        try:
            with patch.object(self.host, "_worker_binding_valid", return_value=True):
                thread = threading.Thread(target=lambda: result.append(
                    self.host.receive_worker_guidance("task-1", "attempt-1")))
                thread.start()
                self.assertTrue(captured.wait(2))
                self.live.request_id = "00000000-0000-4000-8000-000000000099"
        finally:
            state.lock.release()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]["status"], "denied")
        self.assertEqual(state.guidance_inbox.pending_count, 0)

    @staticmethod
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise AssertionError(f"git fixture setup failed ({result.returncode})")
        return result.stdout.strip()

    def make_host(self, *, cancel_event=None, resolver=None, store=None, worker_capabilities=None,
                  human_message_resolver=None,
                  test_full_candidate_coverage=False):
        key = b"stable-host-key-material-long-enough-32-bytes"
        host = _IsolatedTestHost(
            self.config,
            integrity_key=key,
            context_provider=self.live.snapshot,
            trusted_verifier_resolver=resolver or self.verifier_resolver,
            store=store,
            worker_capabilities=worker_capabilities,
            trusted_human_message_resolver=human_message_resolver,
            test_full_candidate_coverage=test_full_candidate_coverage,
        )
        host._workspace = _IsolatedTestWorkspace(self.stable, self.trust_root, key)
        return host

    def verifier_resolver(self, task_id: str, record: dict, config_identity: str, candidate_id: str):
        return WorkspaceBinding(
            task_id=task_id,
            candidate_root=record["candidate_path"],
            candidate_id=candidate_id,
            config_identity=config_identity,
            source_paths=("tracked.txt",),
        )

    def create(self):
        return self.host.create()

    def start_with_persistent_claim(self, host, request):
        import hashlib
        import json
        context = self.live.snapshot()
        payload = json.dumps({"user_goal": request.user_goal,
                              "acceptance_points": list(request.acceptance_points),
                              "context_refs": list(request.context_refs)},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        claim = host._store.reserve_start_request(
            context.conversation_id, context.request_id, hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            run_id=context.run_id, turn_epoch=context.turn_epoch)
        result = host.start(request, start_request_claim=claim)
        if result.get("status") == "unknown":
            host._store.mark_start_request_unknown(claim, {key: value for key, value in result.items()
                                                            if key in {"status", "task_id", "attempt_id", "run_id", "error_code", "worker_bound", "os_sandbox", "merge_authorized"}})
        else:
            host._store.finish_start_request(claim, {key: value for key, value in result.items()
                                                      if key in {"status", "task_id", "attempt_id", "run_id", "error_code", "worker_bound", "os_sandbox", "merge_authorized"}})
        return result

    def test_create_uses_real_git_and_host_owned_run_identity(self) -> None:
        before = self.git("-C", str(self.stable), "rev-parse", "HEAD")
        result = self.create()
        self.assertEqual(result["status"], "created", result)
        self.assertNotEqual(result["task_id"], "")
        self.assertEqual(result["candidate"]["stable_head"], before)
        self.assertEqual(result["candidate"]["candidate_head"], before)
        self.assertFalse(result["candidate"]["os_sandbox"])
        self.assertFalse(result["worker_bound"])
        self.assertFalse(result["merge_authorized"])
        self.assertEqual(result["binding"], {
            "conversation_id": "conversation-1", "run_id": "run-1", "turn_epoch": 7,
            "request_id": "00000000-0000-4000-8000-000000000001", "request_origin": "human",
        })
        self.assertEqual(self.host.inspect(result["task_id"])["status"], "inspected")

    def test_task_cannot_be_used_from_another_conversation_run_or_epoch(self) -> None:
        created = self.create()
        task_id = created["task_id"]
        for field, value in (("conversation_id", "conversation-2"), ("run_id", "run-2"), ("turn_epoch", 8)):
            with self.subTest(field=field):
                old = getattr(self.live, field)
                setattr(self.live, field, value)
                result = self.host.inspect(task_id)
                self.assertEqual(result["status"], "denied")
                self.assertEqual(result["error_code"], "execution_binding_mismatch")
                setattr(self.live, field, old)

    def test_signed_candidate_manifest_tampering_blocks_inspection_and_verification(self) -> None:
        created = self.create()
        task_id = created["task_id"]
        record = self.host._workspace.inspect(task_id)
        manifest = self.trust_root / f"{task_id}.self-evolution-workspace.json"
        manifest.write_text("{\"record\":{},\"hmac_sha256\":\"" + "0" * 64 + "\"}", encoding="utf-8")
        inspected = self.host.inspect(task_id)
        self.assertEqual(inspected["status"], "failed")
        self.assertEqual(inspected["error_code"], "manifest_tampered")
        verified = self.host.verify(task_id)
        self.assertEqual(verified["status"], "failed")
        self.assertFalse(verified["verification_passed"])
        self.assertFalse(verified["merge_authorized"])

    def test_attribution_and_verification_wrap_real_host_evidence(self) -> None:
        created = self.create()
        task_id = created["task_id"]
        record = self.host._workspace.inspect(task_id)
        candidate = Path(record["candidate_path"])
        (candidate / "tracked.txt").write_text("candidate edit\n", encoding="utf-8")
        attribution = self.host.attribute(task_id)
        self.assertEqual(attribution["status"], "observed")
        self.assertIn("tracked.txt", attribution["evidence"]["changed_paths"])
        self.assertEqual(attribution["evidence_scope"], "declared_paths_point_in_time_only")
        self.assertFalse(attribution["full_candidate_coverage"])
        self.assertFalse(attribution["worker_authorization_eligible"])
        self.assertEqual(attribution["evidence"]["evidence_scope"], "declared_paths_point_in_time_only")
        self.assertFalse(attribution["evidence"]["full_candidate_coverage"])
        verified = self.host.verify(task_id)
        self.assertIn(verified["status"], {"verification_incomplete_coverage", "verification_unknown"})
        self.assertFalse(verified["verification_passed"])
        check = verified["evidence"]["checks"][0]
        self.assertIn(check["outcome"], {"passed", "unknown"})
        self.assertEqual(check["outcome"] == "passed", check["process_tree_state"] == "stopped"
                         and check["requires_reconciliation"] is False)
        self.assertFalse(check["full_candidate_coverage"])
        expected_scope = ("declared_source_paths_and_windows_job_tree" if os.name == "nt"
                          else "declared_source_paths_point_in_time_only")
        self.assertEqual(verified["evidence"]["checks"][0]["evidence_scope"], expected_scope)
        self.assertFalse(verified["evidence"]["checks"][0]["full_candidate_coverage"])
        self.assertTrue(verified["evidence"]["checks"][0]["output_redacted"])
        self.assertNotIn("stdout_excerpt", verified["evidence"]["checks"][0])
        self.assertFalse(verified["merge_authorized"])
        self.assertFalse(verified["worker_bound"])

    def test_cancelled_verification_never_becomes_passed(self) -> None:
        event = self.live.cancel_event = threading.Event()
        started = threading.Event()
        self.config = SelfEvolutionHostConfig(
            stable_repository=self.stable,
            trust_root=self.trust_root,
            attribution_paths=("tracked.txt",),
            verification_source_paths=("tracked.txt",),
            verification_commands={"wait": (sys.executable, "-c", "import time; time.sleep(5)")},
            verification_timeout_seconds=8,
        )
        def resolver(task_id: str, record: dict, config_identity: str, candidate_id: str):
            started.set()
            return self.verifier_resolver(task_id, record, config_identity, candidate_id)
        self.host = self.make_host(resolver=resolver)
        # A new host cannot adopt a prior host's task silently; create with its own
        # signed binding before exercising cancellation against the trusted command.
        created = self.create()
        result_box = []
        thread = threading.Thread(target=lambda: result_box.append(self.host.verify(created["task_id"])))
        thread.start()
        self.assertTrue(started.wait(30))
        time.sleep(0.15)
        event.set()
        thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result_box[0]["status"], "unknown")
        self.assertFalse(result_box[0]["verification_passed"])
        self.assertEqual(result_box[0]["error_code"], "cancelled")
        self.assertEqual(result_box[0]["evidence"]["checks"][0]["outcome"], "unknown")
        self.assertFalse(result_box[0]["merge_authorized"])

    def test_inactive_or_cancelled_context_blocks_creation(self) -> None:
        self.live.active = False
        denied = self.create()
        self.assertEqual(denied["status"], "denied")
        self.assertEqual(denied["error_code"], "run_not_live")
        self.assertEqual(list(self.trust_root.iterdir()), [])
        self.live.active = True
        self.live.cancel_event = threading.Event()
        self.live.cancel_event.set()
        cancelled = self.create()
        self.assertEqual(cancelled["status"], "denied")
        self.assertEqual(cancelled["error_code"], "cancelled")
        self.assertEqual(list(self.trust_root.iterdir()), [])

    def test_production_workspace_gate_remains_closed_without_test_substitute(self) -> None:
        key = b"stable-host-key-material-long-enough-32-bytes"
        production_host = SelfEvolutionHost(
            self.config, integrity_key=key, context_provider=self.live.snapshot,
            trusted_verifier_resolver=self.verifier_resolver,
            store=_test_store(self.root / "production-gate.sqlite3"),
            worker_capabilities=_worker_capabilities(_WorkerClient(), stopped=True),
        )
        create_result = production_host.create()
        self.assertEqual(create_result["status"], "failed")
        self.assertEqual(create_result["error_code"], "safe_checkout_unavailable")
        self.assertFalse(production_host.production_ready)
        start_result = self.start_with_persistent_claim(production_host, EvolutionRequest("Keep production disabled"))
        self.assertEqual(start_result["status"], "denied")
        self.assertEqual(start_result["error_code"], "full_candidate_manifest_unavailable")
        self.assertEqual(list(self.trust_root.iterdir()), [])

    def test_final_merge_capability_is_not_exposed(self) -> None:
        self.assertFalse(hasattr(self.host, "merge"))
        self.assertFalse(hasattr(self.host, "confirm_merge"))


    def test_lost_turn_after_candidate_creation_cannot_start_worker(self) -> None:
        client = _WorkerClient()
        host = self.make_host(store=_test_store(self.root / "turn-revoked.sqlite3"),
                              worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        create = host.create
        def create_then_revoke(*args, **kwargs):
            result = create(*args, **kwargs)
            self.live.active = False
            return result
        host.create = create_then_revoke
        result = self.start_with_persistent_claim(host, EvolutionRequest("Improve the parser"))
        self.assertNotEqual(result["status"], "started")
        self.assertEqual(client.calls, 0)

    def test_turn_revoked_after_writer_claim_is_quarantined_without_start(self) -> None:
        client = _WorkerClient()
        store = _test_store(self.root / "writer-revoked.sqlite3")
        host = self.make_host(store=store, worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        claim = store.create_task_with_writer
        def claim_then_revoke(*args, **kwargs):
            result = claim(*args, **kwargs)
            self.live.active = False
            return result
        store.create_task_with_writer = claim_then_revoke
        result = self.start_with_persistent_claim(host, EvolutionRequest("Improve the parser"))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(client.calls, 0)
        self.assertTrue(host.writer_held(result["task_id"]))
        self.live.active = True
        self.assertEqual(host.get_snapshot(result["task_id"]).phase, "unknown")

    def test_uncertain_atomic_task_writer_commit_is_quarantined_unknown(self) -> None:
        client = _WorkerClient()
        store = _test_store(self.root / "ambiguous-writer-commit.sqlite3")
        host = self.make_host(store=store, worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        original = store.create_task_with_writer
        def commit_then_raise(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("simulated response loss after commit")
        store.create_task_with_writer = commit_then_raise

        result = self.start_with_persistent_claim(host, EvolutionRequest("Improve safely"))

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(store.read_task("conversation-1", result["task_id"]).phase, "unknown")
        self.assertEqual(store.list_writer_slots()[0]["state"], "unknown")
        self.assertEqual(client.calls, 0)

    def test_parent_cancel_after_final_live_check_before_thread_start_blocks_model_and_tools(self) -> None:
        client = _WorkerClient()
        tool_calls = []
        tools = tuple(HostTool(tool.name, tool.description,
                               lambda ctx, args, tool=tool: (tool_calls.append(tool.name), tool.invoke(ctx, args))[1])
                      for tool in _worker_tools())
        self.live.cancel_event = threading.Event()
        host = self.make_host(store=_test_store(self.root / "start-race.sqlite3"),
                              worker_capabilities=_worker_capabilities(client, stopped=True, tools=tools),
                              test_full_candidate_coverage=True)
        original_start = threading.Thread.start
        entered, release = threading.Event(), threading.Event()
        result_box = []
        def start_at_barrier(thread):
            if thread.name.startswith("self-evolution-"):
                entered.set()
                if not release.wait(30):
                    raise AssertionError("test start barrier timed out")
            return original_start(thread)
        def invoke_start():
            result_box.append(self.start_with_persistent_claim(host, EvolutionRequest("Improve parser")))
        with patch.object(threading.Thread, "start", start_at_barrier):
            caller = threading.Thread(target=invoke_start, name="test-caller")
            caller.start()
            try:
                self.assertTrue(entered.wait(30), "worker did not reach start barrier")
                self.live.cancel_event.set()
            finally:
                release.set()
                caller.join(30)
        self.assertFalse(caller.is_alive())
        self.assertEqual(len(result_box), 1)
        result = result_box[0]
        state = host._tasks[result["task_id"]]
        if state.worker_thread is not None:
            state.worker_thread.join(30)
            self.assertFalse(state.worker_thread.is_alive())
        self.assertNotEqual(result["status"], "started")
        self.assertEqual(client.calls, 0)
        self.assertEqual(tool_calls, [])
        self.assertTrue(state.cancel_event.is_set())
        self.assertTrue(host.writer_held(result["task_id"]))
        self.live.cancel_event.clear()
        self.assertTrue(state.cancel_event.is_set(), "a later parent turn must not clear cancellation")
        self.assertEqual(host.get_snapshot(result["task_id"]).phase, "unknown")

    def test_later_parent_turn_cancellation_does_not_cancel_handed_off_worker(self) -> None:
        entered, release = threading.Event(), threading.Event()
        client = _BlockingWorkerClient(entered, release)
        self.live.cancel_event = threading.Event()
        host = self.make_host(store=_test_store(self.root / "handoff.sqlite3"),
                              worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        result = self.start_with_persistent_claim(host, EvolutionRequest("Improve parser"))
        try:
            self.assertEqual(result["status"], "started")
            self.assertTrue(entered.wait(30))
            state = host._tasks[result["task_id"]]
            # The bridge reuses this event in later turns. After the handoff it
            # must not silently cancel a different, already accepted task.
            self.live.cancel_event.set()
            self.assertFalse(state.cancel_event.is_set())
        finally:
            release.set()
            if result.get("status") == "started":
                host.wait_for_worker(result["task_id"], result["attempt_id"], timeout=30)
        self.assertGreater(client.calls, 0)

    def test_start_fails_closed_without_explicit_worker_capabilities(self) -> None:
        result = self.host.start(EvolutionRequest("Improve the agent"))
        self.assertEqual(result["status"], "denied")
        self.assertEqual(result["error_code"], "worker_capabilities_unavailable")
        self.assertEqual(list(self.trust_root.iterdir()), [])

    def test_start_consumes_claim_before_candidate_creation_and_stops_on_owner_conflict(self) -> None:
        from self_evolution_store import StartRequestClaim, StoreOwnershipError

        store = _test_store(self.root / "host-consume-conflict.sqlite3")
        client = _WorkerClient()
        host = self.make_host(store=store, worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        claim = StartRequestClaim("conversation-1", self.live.request_id, "a" * 64,
                                  "claimed", "host-test-token")
        calls = []
        def reject_consume(*args, **kwargs):
            calls.append((args, kwargs))
            raise StoreOwnershipError("another durable owner is unresolved")
        store.consume_start_request_claim = reject_consume
        create = host._workspace.create
        def record_create(*args, **kwargs):
            calls.append("candidate_create")
            return create(*args, **kwargs)
        host._workspace.create = record_create

        result = host.start(EvolutionRequest("Do not start while ownership is unresolved"),
                            start_request_claim=claim)

        self.assertEqual(len(calls), 1, calls)
        self.assertIsInstance(calls[0], tuple, calls)
        self.assertEqual(calls[0][1], {"session_id": "conversation-1", "run_id": "run-1", "turn_epoch": 7})
        self.assertEqual(result.get("status"), "denied")
        self.assertEqual(result.get("error_code"), "persistent_start_claim_rejected")
        self.assertFalse(any(item == "candidate_create" for item in calls))
        self.assertEqual(client.calls, 0)
        self.assertEqual(list(self.trust_root.iterdir()), [])

    def test_start_consumes_claim_before_any_candidate_side_effect(self) -> None:
        from self_evolution_store import StartRequestClaim

        store = _test_store(self.root / "host-consume-order.sqlite3")
        client = _WorkerClient()
        host = self.make_host(store=store, worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        claim = StartRequestClaim("conversation-1", self.live.request_id, "b" * 64,
                                  "claimed", "host-test-token")
        order = []
        def consume(*args, **kwargs):
            order.append("consume")
        store.consume_start_request_claim = consume
        def reject_candidate(*args, **kwargs):
            order.append("candidate_create")
            raise WorkspaceError("test stops at first side effect", code="test_boundary", stage="create")
        host._workspace.create = reject_candidate

        result = host.start(EvolutionRequest("Keep consume before create"), start_request_claim=claim)

        self.assertEqual(order, ["consume", "candidate_create"])
        self.assertEqual(result.get("error_code"), "candidate_creation_unknown")
        self.assertEqual(client.calls, 0)

    def test_context_switch_between_start_check_and_create_does_not_create_candidate(self) -> None:
        store = _test_store(self.root / "host-context-switch.sqlite3")
        client = _WorkerClient()
        host = self.make_host(store=store, worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        original = self.live.snapshot()
        switched = replace(original, conversation_id="conversation-2", run_id="run-2")
        reads = 0

        def context_provider():
            nonlocal reads
            reads += 1
            return original if reads <= 3 else switched

        host._context_provider = context_provider
        result = self.start_with_persistent_claim(host, EvolutionRequest("Do not cross contexts"))

        self.assertEqual(result["status"], "denied", result)
        self.assertEqual(result["error_code"], "execution_binding_mismatch")
        self.assertEqual(reads, 4)
        self.assertEqual(list(self.trust_root.iterdir()), [])
        self.assertEqual(client.calls, 0)

    def test_start_rechecks_self_evolution_intent_after_claim_before_candidate_creation(self) -> None:
        store = _test_store(self.root / "host-intent-revoked.sqlite3")
        client = _WorkerClient()
        host = self.make_host(store=store, worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        host._run_worker = lambda *_args: None
        original = self.live.snapshot()
        revoked = replace(original, self_evolution_intent=False)
        reads = 0

        def context_provider():
            nonlocal reads
            reads += 1
            return original if reads <= 3 else revoked

        host._context_provider = context_provider
        result = self.start_with_persistent_claim(host, EvolutionRequest("Intent revocation must stop start"))

        self.assertEqual(result["status"], "denied", result)
        self.assertEqual(result["error_code"], "explicit_self_evolution_intent_required")
        self.assertEqual(list(self.trust_root.iterdir()), [])
        self.assertEqual(store.list_writer_slots(), ())
        self.assertEqual(client.calls, 0)

    def test_ordinary_create_remains_available_without_self_evolution_intent(self) -> None:
        self.live.self_evolution_intent = False

        result = self.create()

        self.assertEqual(result["status"], "created", result)

    def test_intent_revoked_during_candidate_creation_prevents_writer_claim(self) -> None:
        store = _test_store(self.root / "host-intent-during-create.sqlite3")
        client = _WorkerClient()
        host = self.make_host(store=store, worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        create = host._workspace.create

        def create_then_revoke_intent():
            result = create()
            self.live.self_evolution_intent = False
            return result

        host._workspace.create = create_then_revoke_intent
        result = self.start_with_persistent_claim(host, EvolutionRequest("Recheck intent before writer"))

        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["error_code"], "candidate_created_context_ended")
        self.assertTrue(list(self.trust_root.iterdir()))
        self.assertEqual(store.list_writer_slots(), ())
        self.assertEqual(client.calls, 0)

    def test_intent_revoked_after_writer_claim_quarantines_without_dispatch(self) -> None:
        store = _test_store(self.root / "host-intent-after-writer.sqlite3")
        client = _WorkerClient()
        host = self.make_host(store=store, worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        claim = store.create_task_with_writer

        def claim_then_revoke_intent(*args, **kwargs):
            result = claim(*args, **kwargs)
            self.live.self_evolution_intent = False
            return result

        store.create_task_with_writer = claim_then_revoke_intent
        result = self.start_with_persistent_claim(host, EvolutionRequest("Recheck intent before dispatch"))
        state = host._tasks.get(result.get("task_id"))
        if state is not None and state.worker_thread is not None:
            state.worker_thread.join(10)
            self.assertFalse(state.worker_thread.is_alive())

        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(client.calls, 0)
        self.assertTrue(host.writer_held(result["task_id"]))
        self.assertEqual(host.get_snapshot(result["task_id"]).phase, "unknown")
        self.assertEqual(store.list_writer_slots()[0]["state"], "unknown")

    def test_start_persists_session_bound_task_and_dispatches_only_trusted_worker(self) -> None:
        client = _WorkerClient()
        host = self.make_host(store=_test_store(self.root / "host.sqlite3"), worker_capabilities=_worker_capabilities(client, stopped=True),
                              test_full_candidate_coverage=True)
        started = self.start_with_persistent_claim(host, EvolutionRequest("Improve the parser", ("Keep backwards compatibility",)))
        self.assertEqual(started["status"], "started", started)
        task_id, attempt_id = started["task_id"], started["attempt_id"]
        host.wait_for_worker(task_id, attempt_id, timeout=90)
        snapshot = host.get_snapshot(task_id)
        self.assertEqual(snapshot.phase, "unknown", snapshot)
        self.assertTrue(host.writer_held(task_id))
        self.assertEqual(snapshot.identity.session_id, "conversation-1")
        self.assertNotEqual(host.get_snapshot(task_id, session_id="other-session"), snapshot)
        self.assertEqual(client.calls, 4)
        events = host._store.read_events("conversation-1", task_id, attempt_id)
        milestones = [(item["kind"], tuple(item["fact_codes"]), tuple(item["evidence_refs"]))
                      for item in events if item["fact_codes"]]
        self.assertEqual([item[0] for item in milestones], ["phase_changed"] * 4 + ["task_terminal"])
        self.assertEqual([item[1] for item in milestones], [
            ("task_accepted",), ("observation_recorded",), ("edit_started",),
            ("check_started",), ("unknown",),
        ])
        self.assertEqual(milestones[0][2], ())
        self.assertTrue(all(item[2] for item in milestones[1:-1]))
        self.assertEqual(milestones[-1][1], ("unknown",))
        self.assertEqual(milestones[-1][2], ())
        self.assertFalse(host.merge_authorized)

    def test_projected_events_advance_reducer_watermarks_for_each_milestone(self) -> None:
        store = _test_store(self.root / "host.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "task-synthetic", "attempt-synthetic", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="candidate_digest_1",
                                    allowed_next_actions=("cancel", "get_snapshot"))
        store.create_task(identity, initial)
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=threading.Event(), owner_id="synthetic-owner")
        host._tasks[identity.task_id] = state
        host._append_fact_event(state, "preparing", ("task_accepted",))
        host._persist_worker_phase(state, "observing")
        host._record_tool_milestone(state, "observe_candidate", {},
                                    {"status": "observed", "evidence_ref": "private://candidate/observation"})
        host._persist_worker_phase(state, "editing")
        host._record_tool_milestone(state, "write_candidate_file", {},
                                    {"status": "written", "evidence_ref": "C:/private/candidate.diff"})
        host._persist_worker_phase(state, "checking")
        host._current_candidate_digest = lambda _state: "candidate_digest_1"
        host._record_tool_milestone(state, "run_registered_check", {"check_id": "syntax"},
                                    {"status": "passed", "check_id": "syntax",
                                     "candidate_digest": "candidate_digest_1",
                                     "evidence_ref": "private://check/result"})
        host._persist_worker_phase(state, "awaiting_review")
        events = store.read_events(identity.session_id, identity.task_id, identity.attempt_id)
        persisted = tuple(EvolutionEvent(identity, item["sequence"], item["phase"], item["kind"],
                                         tuple(item["fact_codes"]), item["snapshot_revision"],
                                         tuple(item["evidence_refs"])) for item in events)
        projected = project_evolution(initial, authorized_session_id=identity.session_id,
                                      last_sequence=0, events=persisted)
        view = projected["snapshot"]
        seen_sequences, seen_revisions, facts = [], [], []
        for event in projected["events"]:
            # Mirror SelfEvolutionProgress.accept's identity and strict watermark gate.
            self.assertEqual(event["type"], "self_evolution.update")
            self.assertEqual(event["session_id"], view["session_id"])
            self.assertEqual(event["task_id"], view["task_id"])
            self.assertEqual(event["attempt_id"], view["attempt_id"])
            self.assertEqual(event["run_id"], view["run_id"])
            self.assertGreater(event["sequence"], view["sequence"])
            self.assertGreater(event["snapshot_revision"], view["snapshot_revision"])
            self.assertTrue(all(code not in {"observation_recorded", "edit_started", "check_started"}
                                or event["evidence_refs"] for code in event["fact_codes"]))
            if event["fact_codes"]:
                self.assertTrue(all(ref.isalnum() for ref in event["evidence_refs"]))
            view = {**view, "sequence": event["sequence"], "snapshot_revision": event["snapshot_revision"],
                    "phase": event["phase"]}
            seen_sequences.append(view["sequence"])
            seen_revisions.append(view["snapshot_revision"])
            facts.extend(event["fact_codes"])
        self.assertEqual(seen_sequences, sorted(set(seen_sequences)))
        self.assertEqual(seen_revisions, sorted(set(seen_revisions)))
        self.assertTrue({"task_accepted", "observation_recorded", "edit_started", "check_started"}.issubset(facts))
        # Only Host settlement may publish awaiting_review, with validated evidence.
        self.assertEqual(events[-1]["phase"], "checking")
        self.assertNotIn("awaiting_review", [event["phase"] for event in events])

    def test_experimental_start_is_off_by_default_even_with_worker_wiring(self) -> None:
        from self_evolution_host import TrustedWorkerCapabilities
        client = _WorkerClient()
        caps = TrustedWorkerCapabilities(client=client, model_id="trusted-model",
                                         outbound_request_confirm_fn=lambda _preview, _allow_all: "y",
                                         tools=_worker_tools(), process_tree_stopped=lambda _identity: True)
        host = self.make_host(store=_test_store(self.root / "host.sqlite3"), worker_capabilities=caps)
        result = host.start(EvolutionRequest("Do not dispatch by default"))
        self.assertEqual(result["status"], "denied")
        self.assertEqual(result["error_code"], "self_evolution_host_not_production_ready")
        self.assertEqual(list(self.trust_root.iterdir()), [])
        self.assertEqual(client.calls, 0)

    def test_start_requires_explicit_self_evolution_intent_before_candidate_creation(self) -> None:
        from self_evolution_host import TrustedWorkerCapabilities
        client = _WorkerClient()
        caps = TrustedWorkerCapabilities(client=client, model_id="trusted-model",
                                         outbound_request_confirm_fn=lambda _preview, _allow_all: "y",
                                         tools=_worker_tools(), process_tree_stopped=lambda _identity: True,
                                         enable_experimental_start=True)
        self.live.self_evolution_intent = False
        host = self.make_host(store=_test_store(self.root / "host.sqlite3"), worker_capabilities=caps)
        result = host.start(EvolutionRequest("This is ordinary chat"))
        self.assertEqual(result["status"], "denied")
        self.assertEqual(result["error_code"], "explicit_self_evolution_intent_required")
        self.assertEqual(list(self.trust_root.iterdir()), [])
        self.assertEqual(client.calls, 0)

    def test_cancel_from_new_human_run_is_session_and_attempt_bound_not_stop_confirmation(self) -> None:
        store = _test_store(self.root / "host.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "task-cancel", "attempt-cancel", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="candidate_digest_1",
                                    allowed_next_actions=("cancel", "get_snapshot"))
        store.create_task(identity, initial)
        owner = "cancel-owner"
        store.claim_writer(owner, identity.task_id, identity.attempt_id)
        cancel_event = threading.Event()
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=cancel_event, owner_id=owner, writer_held=True)
        host._tasks[identity.task_id] = state
        self.live.conversation_id = "conversation-other"
        denied = host.request_cancel(identity.task_id, identity.attempt_id)
        self.assertEqual(denied.disposition, "not_found")
        self.assertFalse(cancel_event.is_set())
        self.live.conversation_id = "conversation-1"
        self.live.run_id = "new-human-run"
        self.live.turn_epoch = 8
        self.live.request_id = "00000000-0000-4000-8000-000000000008"
        stale_attempt = host.request_cancel(identity.task_id, "stale-attempt")
        self.assertEqual(stale_attempt.disposition, "not_found")
        self.assertFalse(cancel_event.is_set())
        accepted = host.request_cancel(identity.task_id, identity.attempt_id)
        self.assertEqual(accepted.disposition, "accepted")
        self.assertTrue(cancel_event.is_set())
        snapshot = store.read_task(identity.session_id, identity.task_id)
        event = store.read_events(identity.session_id, identity.task_id, identity.attempt_id)[0]
        self.assertEqual(snapshot.phase, "cancelling")
        self.assertEqual((event["phase"], event["snapshot_revision"]),
                         (snapshot.phase, snapshot.snapshot_revision))
        self.assertTrue(store.mark_orphaned_unknown(owner))

    def test_restart_preserves_awaiting_review_snapshot_after_confirmed_writer_release(self) -> None:
        store = _test_store(self.root / "host-review-restart.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "task-review-restart", "attempt-review-restart", "run-1", 7)
        review = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="candidate_digest_1")
        store.create_task(identity, review)
        for revision, phase in ((2, "observing"), (3, "editing"), (4, "checking"), (5, "awaiting_review")):
            review = replace(review, phase=phase, snapshot_revision=revision,
                             allowed_next_actions=("revise", "get_snapshot") if phase == "awaiting_review" else ())
            store.compare_and_swap(identity.task_id, revision - 1, review)

        recovered = host.get_snapshot(identity.task_id)

        self.assertEqual(recovered, review)
        self.assertEqual(store.read_events(identity.session_id, identity.task_id, identity.attempt_id), ())

    def test_second_host_cannot_rewrite_active_snapshot_without_owner_proof(self) -> None:
        store = _test_store(self.root / "host.sqlite3")
        identity = EvolutionIdentity("conversation-1", "task-restart", "attempt-restart", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="candidate_digest_1",
                                    allowed_next_actions=("cancel", "get_snapshot"))
        store.create_task(identity, initial)
        store.claim_writer("owner-restart", identity.task_id, identity.attempt_id)
        restarted = self.make_host(store=store)
        self.assertIsNone(restarted.get_snapshot(identity.task_id))
        self.assertEqual(restarted.create()["error_code"], "startup_state_unresolved")
        self.assertEqual(restarted.start(EvolutionRequest("must not retry"))["error_code"],
                         "startup_state_unresolved")
        self.assertEqual(store.read_task(identity.session_id, identity.task_id), initial)
        self.assertEqual(store.list_writer_slots()[0]["owner_id"], "owner-restart")
        with self.assertRaises(StoreOwnershipError):
            store.claim_writer("replacement-owner", identity.task_id, identity.attempt_id)

    def test_unknown_transition_emits_one_host_event_when_store_does_not_own_writer(self) -> None:
        store = _test_store(self.root / "host.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "task-unknown", "attempt-unknown", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1,
                                    allowed_next_actions=("cancel", "get_snapshot"))
        store.create_task(identity, initial)
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=threading.Event())
        host._tasks[identity.task_id] = state
        host._mark_unknown(state, "restart_not_confirmed")
        host._mark_unknown(state, "restart_not_confirmed_again")
        snapshot = store.read_task(identity.session_id, identity.task_id)
        events = store.read_events(identity.session_id, identity.task_id, identity.attempt_id)
        self.assertEqual(snapshot.phase, "unknown")
        self.assertEqual(snapshot.error_code, "restart_not_confirmed")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["kind"], events[0]["fact_codes"]), ("task_terminal", ["unknown"]))
        self.assertEqual(events[0]["snapshot_revision"], snapshot.snapshot_revision)

    def test_unknown_with_writer_delegates_single_atomic_event_to_store(self) -> None:
        store = _test_store(self.root / "host.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "task-orphan", "attempt-orphan", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="candidate_digest_1",
                                    allowed_next_actions=("cancel", "get_snapshot"))
        store.create_task(identity, initial)
        owner = "orphan-owner"
        store.claim_writer(owner, identity.task_id, identity.attempt_id)
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=threading.Event(), owner_id=owner,
                           writer_held=True)
        host._tasks[identity.task_id] = state
        host._mark_unknown(state, "host_interrupted")
        snapshot = store.read_task(identity.session_id, identity.task_id)
        events = store.read_events(identity.session_id, identity.task_id, identity.attempt_id)
        self.assertEqual(snapshot.phase, "unknown")
        self.assertEqual(snapshot.blocker_code, "writer_owner_unconfirmed")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["kind"], events[0]["fact_codes"]), ("task_terminal", ["unknown"]))
        self.assertEqual(events[0]["snapshot_revision"], snapshot.snapshot_revision)
        self.assertEqual(events[0]["sequence"], 1)

    def test_unresolved_check_persists_unknown_and_retains_writer_slot(self) -> None:
        import sqlite3

        store = _test_store(self.root / "host-reconcile.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "task-reconcile", "attempt-reconcile", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="candidate_digest_1")
        store.create_task(identity, initial)
        for revision, phase in ((2, "observing"), (3, "editing"), (4, "checking")):
            store.compare_and_swap(identity.task_id, revision - 1,
                                   replace(initial, phase=phase, snapshot_revision=revision))
        owner = "owner-reconcile"
        store.claim_writer(owner, identity.task_id, identity.attempt_id)
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=threading.Event(), owner_id=owner,
                           writer_held=True)
        host._tasks[identity.task_id] = state
        host._current_candidate_digest = lambda _state: "candidate_digest_1"
        state.check_evidence_provenance["syntax"] = {
            "status": "passed", "candidate_digest": "candidate_digest_1", "exit_code": 0,
            "process_tree_state": "stopped", "requires_reconciliation": False,
            "evidence_scope": "declared_source_paths_and_windows_job_tree",
        }
        result = SelfEvolutionWorkerResult(
            status="blocked", phase="checking", session_id=identity.session_id,
            task_id=identity.task_id, attempt_id=identity.attempt_id, run_id=identity.run_id,
            candidate_id=identity.task_id, candidate_digest="candidate_digest_1",
            check_evidence=({"check_id": "syntax", "status": "passed",
                             "candidate_digest": "candidate_digest_1", "evidence_ref": "opaque",
                             "exit_code": 0, "process_tree_state": "unknown",
                             "requires_reconciliation": True},),
        )

        host._settle_worker_result(state, result, stopped_confirmed=True)

        snapshot = store.read_task(identity.session_id, identity.task_id)
        self.assertEqual(snapshot.phase, "unknown")
        self.assertEqual(snapshot.blocker_code, "verification_requires_reconciliation")
        self.assertEqual(snapshot.evidence[0].exit_code, 0)
        self.assertEqual(snapshot.evidence[0].status, "unknown")
        self.assertEqual(snapshot.evidence[0].process_tree_state, "unknown")
        self.assertTrue(state.writer_held)
        conn = sqlite3.connect(store.path)
        try:
            self.assertEqual(conn.execute("SELECT state FROM writer_slot WHERE slot=1").fetchone()[0], "unknown")
        finally:
            conn.close()

    def test_attribution_scope_declaration_is_bound_into_summary_digest(self) -> None:
        import hashlib
        import json
        from self_evolution_host import _scope_attribution_evidence

        scoped = _scope_attribution_evidence({"schema": "attribution-test", "changed_paths": ["declared.py"],
                                              "summary_sha256": "old-unscoped-digest"})
        digest_payload = {key: value for key, value in scoped.items() if key != "summary_sha256"}
        expected = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, separators=(",", ":"),
                                             ensure_ascii=True).encode("utf-8")).hexdigest()

        self.assertEqual(scoped["evidence_scope"], "declared_paths_point_in_time_only")
        self.assertFalse(scoped["full_candidate_coverage"])
        self.assertFalse(scoped["worker_authorization_eligible"])
        self.assertEqual(scoped["summary_sha256"], expected)
        self.assertNotEqual(scoped["summary_sha256"], "old-unscoped-digest")

    def test_worker_start_is_denied_without_full_candidate_manifest(self) -> None:
        client = _WorkerClient()
        store = _test_store(self.root / "host-no-manifest.sqlite3")
        host = self.make_host(store=store,
                             worker_capabilities=_worker_capabilities(client, stopped=True))

        started = self.start_with_persistent_claim(host, EvolutionRequest("Improve the parser"))

        self.assertEqual(started["status"], "denied")
        self.assertEqual(started["error_code"], "full_candidate_manifest_unavailable")
        self.assertEqual(client.calls, 0)
        self.assertEqual(list(self.trust_root.iterdir()), [])

    def test_worker_phase_event_failure_never_persists_phase_without_its_event(self) -> None:
        import sqlite3

        store = _test_store(self.root / "host-atomic.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "task-atomic", "attempt-atomic", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="candidate_digest_1")
        store.create_task(identity, initial)
        owner = "owner-atomic"
        store.claim_writer(owner, identity.task_id, identity.attempt_id)
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=threading.Event(), owner_id=owner,
                           writer_held=True)
        host._tasks[identity.task_id] = state
        conn = sqlite3.connect(store.path)
        try:
            conn.execute("""CREATE TRIGGER reject_observing_event BEFORE INSERT ON events
                           WHEN json_extract(NEW.payload, '$.phase') = 'observing'
                           BEGIN SELECT RAISE(ABORT, 'event unavailable'); END""")
        finally:
            conn.close()

        with self.assertRaises(sqlite3.IntegrityError):
            host._persist_worker_phase(state, "observing")

        snapshot = store.read_task(identity.session_id, identity.task_id)
        events = store.read_events(identity.session_id, identity.task_id, identity.attempt_id)
        self.assertEqual(snapshot.phase, "unknown")
        self.assertEqual([event["phase"] for event in events], ["unknown"])
        self.assertEqual(events[0]["snapshot_revision"], snapshot.snapshot_revision)
        self.assertEqual(events[0]["sequence"], 1)
        with self.assertRaises(StoreOwnershipError):
            store.claim_writer("replacement-owner", identity.task_id, identity.attempt_id)

    def test_settlement_does_not_accept_model_summary_and_binds_unknown_to_candidate(self) -> None:
        store = _test_store(self.root / "host.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "task-settle", "attempt-settle", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="candidate_digest_old",
                                    change_summary="Old digest summary.",
                                    evidence=(VerificationEvidence("old_check", "candidate_digest_old", "passed",
                                                                   0, "stopped", "old_evidence"),),
                                    allowed_next_actions=("cancel", "get_snapshot"))
        store.create_task(identity, initial)
        observing = replace(initial, phase="observing", snapshot_revision=2)
        store.compare_and_swap(identity.task_id, 1, observing)
        editing = replace(observing, phase="editing", snapshot_revision=3)
        store.compare_and_swap(identity.task_id, 2, editing)
        checking = replace(editing, phase="checking", snapshot_revision=4)
        store.compare_and_swap(identity.task_id, 3, checking)
        owner = "synthetic-owner"
        store.claim_writer(owner, identity.task_id, identity.attempt_id)
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=threading.Event(), owner_id=owner,
                           writer_held=True)
        host._tasks[identity.task_id] = state
        host._current_candidate_digest = lambda _state: "candidate_digest_1"
        state.check_evidence_provenance["syntax"] = {
            "status": "passed", "candidate_digest": "candidate_digest_1", "exit_code": 0,
            "process_tree_state": "stopped", "requires_reconciliation": False,
            "evidence_scope": "declared_source_paths_and_windows_job_tree",
        }
        result = SelfEvolutionWorkerResult(
            status="finished", phase="awaiting_review", session_id=identity.session_id,
            task_id=identity.task_id, attempt_id=identity.attempt_id, run_id=identity.run_id,
            candidate_id=identity.task_id, candidate_digest="candidate_digest_1",
            final_text="UNTRUSTED MODEL CLAIM: reviewed private file contents",
            check_evidence=({"check_id": "syntax", "status": "passed",
                             "candidate_digest": "candidate_digest_1",
                             "evidence_ref": "private://raw/check", "exit_code": 0,
                             "process_tree_state": "stopped", "requires_reconciliation": False,
                             "evidence_scope": "declared_source_paths_and_windows_job_tree"},),
        )
        with patch.object(store, "commit_transition", wraps=store.commit_transition) as commit:
            host._settle_worker_result(state, result, stopped_confirmed=True)
        snapshot = store.read_task(identity.session_id, identity.task_id)
        events = store.read_events(identity.session_id, identity.task_id, identity.attempt_id)
        self.assertEqual(snapshot.phase, "unknown")
        self.assertEqual(snapshot.candidate_digest, "candidate_digest_1")
        self.assertEqual(snapshot.evidence[0].candidate_digest, snapshot.candidate_digest)
        self.assertTrue(snapshot.evidence[0].evidence_ref.isalnum())
        self.assertIsNone(snapshot.change_summary)
        self.assertEqual(commit.call_count, 1)
        committed = commit.call_args.args[2]
        self.assertEqual(committed.candidate_digest, "candidate_digest_1")
        self.assertEqual(committed.evidence[0].candidate_digest, "candidate_digest_1")
        self.assertEqual(committed.snapshot_revision, snapshot.snapshot_revision)
        self.assertIsNone(commit.call_args.kwargs["release_owner_id"])
        self.assertEqual(commit.call_args.kwargs["quarantine_owner_id"], owner)
        self.assertIs(commit.call_args.kwargs["stop_confirmed"], False)
        self.assertTrue(state.writer_held)
        self.assertEqual(events[0]["snapshot_revision"], snapshot.snapshot_revision)
        self.assertEqual(events[0]["fact_codes"], ["unknown"])

    def test_observation_node_requires_host_evidence_reference(self) -> None:
        client = _WorkerClient()
        tools = tuple(HostTool(tool.name, tool.description,
                               (lambda _context, _args: {"status": "observed"})
                               if tool.name == "observe_candidate" else tool.invoke)
                      for tool in _worker_tools())
        host = self.make_host(store=_test_store(self.root / "host.sqlite3"),
                              worker_capabilities=_worker_capabilities(client, stopped=True, tools=tools),
                              test_full_candidate_coverage=True)
        started = self.start_with_persistent_claim(host, EvolutionRequest("Improve the parser"))
        self.assertEqual(started["status"], "started", started)
        host.wait_for_worker(started["task_id"], started["attempt_id"], timeout=90)
        events = host._store.read_events("conversation-1", started["task_id"], started["attempt_id"])
        self.assertFalse(any("observation_recorded" in item["fact_codes"] for item in events))

    def test_cancel_receipt_is_not_reported_as_stop_confirmation(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        client = _BlockingWorkerClient(entered, release)
        host = self.make_host(store=_test_store(self.root / "host.sqlite3"), worker_capabilities=_worker_capabilities(client, stopped=False),
                              test_full_candidate_coverage=True)
        started = self.start_with_persistent_claim(host, EvolutionRequest("Improve the parser"))
        self.assertEqual(started["status"], "started", started)
        self.assertTrue(entered.wait(30))
        receipt = host.request_cancel(started["task_id"], started["attempt_id"])
        self.assertEqual(receipt.disposition, "accepted")
        release.set()
        host.wait_for_worker(started["task_id"], started["attempt_id"], timeout=90)
        self.assertEqual(host.get_snapshot(started["task_id"]).phase, "unknown")
        self.assertTrue(host.writer_held(started["task_id"]))
    def test_settlement_rejects_job_scope_as_complete_stop_proof(self) -> None:
        for scope, expected_phase in (
            ("declared_source_paths_and_windows_job_tree", "unknown"),
            ("declared_source_paths_point_in_time_only", "unknown"),
            (None, "unknown"),
        ):
            with self.subTest(scope=scope):
                store = _test_store(self.root / f"settle-{scope or 'missing'}.sqlite3")
                host = self.make_host(store=store)
                identity = EvolutionIdentity("conversation-1", f"task-{scope or 'missing'}",
                                             f"attempt-{scope or 'missing'}", "run-1", 7)
                initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="digest",
                                            allowed_next_actions=("cancel", "get_snapshot"))
                store.create_task(identity, initial)
                for revision, phase in ((2, "observing"), (3, "editing"), (4, "checking")):
                    store.compare_and_swap(identity.task_id, revision - 1,
                                           replace(initial, phase=phase, snapshot_revision=revision))
                owner = f"owner-{scope or 'missing'}"
                store.claim_writer(owner, identity.task_id, identity.attempt_id)
                state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                                   identity=identity, cancel_event=threading.Event(), owner_id=owner,
                                   writer_held=True)
                host._tasks[identity.task_id] = state
                host._current_candidate_digest = lambda _state: "digest"
                check = {"check_id": "syntax", "status": "passed", "candidate_digest": "digest",
                         "evidence_ref": "opaque", "exit_code": 99, "process_tree_state": "not_started",
                         "requires_reconciliation": False}
                state.check_evidence_provenance["syntax"] = {
                    "status": "passed", "candidate_digest": "digest", "exit_code": 0,
                    "process_tree_state": "stopped", "requires_reconciliation": False,
                    "evidence_scope": scope or "",
                }
                if scope is not None:
                    check["evidence_scope"] = scope
                result = SelfEvolutionWorkerResult(
                    status="finished", phase="awaiting_review", session_id=identity.session_id,
                    task_id=identity.task_id, attempt_id=identity.attempt_id, run_id=identity.run_id,
                    candidate_id=identity.task_id, candidate_digest="digest", check_evidence=(check,),
                )

                host._settle_worker_result(state, result, stopped_confirmed=True)

                persisted = store.read_task(identity.session_id, identity.task_id)
                self.assertEqual(persisted.phase, expected_phase)
                if expected_phase == "unknown":
                    self.assertEqual(persisted.evidence[0].status, "unknown")
                    self.assertEqual(persisted.evidence[0].process_tree_state, "unknown")
                    self.assertEqual(persisted.evidence[0].exit_code, 0)
                    self.assertTrue(state.writer_held)
                    self.assertEqual(store.list_writer_slots()[0]["state"], "unknown")
                else:
                    self.assertEqual(persisted.evidence[0].status, "passed")
                    self.assertFalse(state.writer_held)
                    self.assertEqual(store.list_writer_slots(), ())

    def test_attempted_check_omitted_by_worker_cannot_release_writer(self) -> None:
        store = _test_store(self.root / "host-omitted-check.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "omitted-check", "attempt-omitted", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="digest")
        store.create_task(identity, initial)
        for revision, phase in ((2, "observing"), (3, "editing"), (4, "checking")):
            store.compare_and_swap(identity.task_id, revision - 1,
                                   replace(initial, phase=phase, snapshot_revision=revision))
        store.claim_writer("owner", identity.task_id, identity.attempt_id)
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=threading.Event(), owner_id="owner",
                           writer_held=True, verification_attempted=True)
        host._tasks[identity.task_id] = state
        host._current_candidate_digest = lambda _state: "digest"
        result = SelfEvolutionWorkerResult(
            status="failed", phase="checking", session_id=identity.session_id,
            task_id=identity.task_id, attempt_id=identity.attempt_id, run_id=identity.run_id,
            candidate_id=identity.task_id, candidate_digest="digest", check_evidence=(),
        )

        host._settle_worker_result(state, result, stopped_confirmed=True)

        persisted = store.read_task(identity.session_id, identity.task_id)
        self.assertEqual(persisted.phase, "unknown")
        self.assertEqual(persisted.blocker_code, "verification_requires_reconciliation")
        self.assertTrue(state.writer_held)
        self.assertEqual(store.list_writer_slots()[0]["state"], "unknown")

    def test_worker_claim_without_host_provenance_is_never_trusted(self) -> None:
        store = _test_store(self.root / "host-untrusted-check.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "untrusted-check", "attempt-untrusted", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, candidate_digest="digest")
        store.create_task(identity, initial)
        for revision, phase in ((2, "observing"), (3, "editing"), (4, "checking")):
            store.compare_and_swap(identity.task_id, revision - 1,
                                   replace(initial, phase=phase, snapshot_revision=revision))
        store.claim_writer("owner", identity.task_id, identity.attempt_id)
        state = _TaskState(identity.task_id, self.live.snapshot().identity(), {}, None, {},
                           identity=identity, cancel_event=threading.Event(), owner_id="owner", writer_held=True)
        host._tasks[identity.task_id] = state
        host._current_candidate_digest = lambda _state: "digest"
        result = SelfEvolutionWorkerResult(
            status="finished", phase="checking", session_id=identity.session_id,
            task_id=identity.task_id, attempt_id=identity.attempt_id, run_id=identity.run_id,
            candidate_id=identity.task_id, candidate_digest="digest", check_evidence=({
                "check_id": "syntax", "status": "passed", "candidate_digest": "digest", "exit_code": 0,
                "process_tree_state": "stopped", "evidence_ref": "forged", "requires_reconciliation": False,
                "evidence_scope": "declared_source_paths_and_windows_job_tree",
            },),
        )

        host._settle_worker_result(state, result, stopped_confirmed=True)

        persisted = store.read_task(identity.session_id, identity.task_id)
        self.assertEqual(persisted.phase, "unknown")
        self.assertEqual(persisted.evidence[0].exit_code, None)
        self.assertEqual(persisted.evidence[0].process_tree_state, "unknown")
        self.assertEqual(persisted.evidence[0].evidence_ref, "worker_check_unconfirmed")
        self.assertEqual(store.list_writer_slots()[0]["state"], "unknown")

    def test_host_event_page_is_session_attempt_bound_and_reports_truncation(self) -> None:
        store = _test_store(self.root / "host-event-page.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "page-task", "page-attempt", "run-1", 7)
        store.create_task(identity, EvolutionSnapshot(identity, "preparing", 1))
        for sequence in range(1, 260):
            store.append_event(EvolutionEvent(identity, sequence, "preparing", "phase_changed", (), 1))

        page = host.get_event_page(identity.task_id, identity.attempt_id)
        self.assertIsNotNone(page)
        self.assertEqual(len(page.events), 256)
        self.assertTrue(page.history_truncated)
        self.assertTrue(page.has_gap)
        self.assertIsNone(host.get_event_page(identity.task_id, identity.attempt_id,
                                              session_id="another-session"))
        self.assertIsNone(host.get_event_page(identity.task_id, "stale-attempt"))

    def test_verification_projection_preserves_windows_job_and_unknown_provenance(self) -> None:
        from self_evolution_host import _verification_dict
        from self_evolution_verification import VerificationResult

        base = dict(outcome="passed", error_code=None, runtime="python", exit_code=0,
                    duration_seconds=0.1, candidate_binding_digest="binding",
                    source_digest_before="before", source_digest_after="after",
                    stdout_excerpt="", stderr_excerpt="", cwd_is_candidate_root=True)
        contained = VerificationResult(**{**base, "outcome": "unknown"}, process_tree_state="unknown",
                                       descendants_may_be_alive=True, requires_reconciliation=True,
                                       evidence_scope="declared_source_paths_and_windows_job_tree",
                                       job_tree_state="stopped")
        unknown = VerificationResult(**{**base, "outcome": "unknown"},
                                     process_tree_state="unknown", descendants_may_be_alive=True,
                                     requires_reconciliation=True,
                                     evidence_scope="declared_source_paths_point_in_time_only")

        contained_projection = _verification_dict(contained)
        unknown_projection = _verification_dict(unknown)
        self.assertEqual(contained_projection["process_tree_state"], "unknown")
        self.assertEqual(contained_projection["job_tree_state"], "stopped")
        self.assertTrue(contained_projection["requires_reconciliation"])
        self.assertEqual(contained_projection["evidence_scope"], "declared_source_paths_and_windows_job_tree")
        self.assertEqual(unknown_projection["process_tree_state"], "unknown")
        self.assertEqual(unknown_projection["job_tree_state"], "not_started")
        self.assertTrue(unknown_projection["descendants_may_be_alive"])
        self.assertTrue(unknown_projection["requires_reconciliation"])
        self.assertEqual(unknown_projection["evidence_scope"], "declared_source_paths_point_in_time_only")

    def test_read_context_rejects_all_host_write_operations(self) -> None:
        from self_evolution_host import HostRunContextProvider

        store = _test_store(self.root / "read-only.sqlite3")
        host = self.make_host(store=store)
        provider = HostRunContextProvider()
        host._context_provider = provider
        identity = EvolutionIdentity("conversation-1", "read-only-task", "attempt-read-only", "run-1", 7)
        initial = EvolutionSnapshot(identity, "preparing", 1, allowed_next_actions=("cancel", "get_snapshot"))
        store.create_task_with_writer(identity, initial, "read-only-owner")
        expected = store.mark_task_unknown(identity.task_id, "read_only_fixture")

        with provider.bind_read("conversation-1", lambda: True):
            self.assertEqual(host.create()["error_code"], "run_not_live")
            self.assertEqual(host.start(EvolutionRequest("must remain read-only"))["error_code"], "run_not_live")
            self.assertEqual(host.verify(identity.task_id)["error_code"], "run_not_live")
            self.assertEqual(host.request_cancel(identity.task_id, identity.attempt_id).disposition, "unknown")
            self.assertEqual(host.get_snapshot(identity.task_id, session_id="conversation-1"), expected)

        self.assertEqual(store.read_task("conversation-1", identity.task_id), expected)
        self.assertEqual(list(self.trust_root.iterdir()), [])

    def test_snapshot_read_does_not_quarantine_a_concurrently_appearing_foreign_owner(self) -> None:
        store = _test_store(self.root / "host-read-foreign-owner.sqlite3")
        host = self.make_host(store=store)
        identity = EvolutionIdentity("conversation-1", "foreign-task", "foreign-attempt", "other-run", 99)
        initial = EvolutionSnapshot(identity, "preparing", 1,
                                    allowed_next_actions=("cancel", "get_snapshot"))
        store.create_task(identity, initial)
        store.claim_writer("foreign-owner", identity.task_id, identity.attempt_id)

        self.assertIsNone(host.get_snapshot(identity.task_id))

        self.assertEqual(store.read_task(identity.session_id, identity.task_id), initial)
        self.assertEqual(store.list_writer_slots(), ({
            "owner_id": "foreign-owner", "task_id": identity.task_id,
            "attempt_id": identity.attempt_id, "state": "held",
        },))
    def test_host_startup_reconciles_legacy_writer_without_unlocking(self) -> None:
        store = _test_store(self.root / "startup.sqlite3")
        identity = EvolutionIdentity("conversation-1", "legacy-task", "legacy-attempt", "run-1", 7)
        store.create_task(identity, EvolutionSnapshot(identity, "preparing", 1,
                                                      allowed_next_actions=("get_snapshot",)))
        store.claim_writer("legacy-owner", identity.task_id, identity.attempt_id)
        host = self.make_host(store=store)

        # A second Host cannot prove the legacy owner is dead; preserve every
        # durable row and fail closed rather than rewriting another Host's state.
        recovered = store.read_task("conversation-1", "legacy-task")
        self.assertEqual(recovered.phase, "preparing")
        self.assertEqual(store.list_writer_slots()[0]["state"], "held")
        self.assertEqual(host.get_snapshot(identity.task_id), None)
        self.assertEqual(host.create()["error_code"], "startup_state_unresolved")
        with self.assertRaises(StoreOwnershipError):
            store.claim_writer("new-owner", identity.task_id, identity.attempt_id)
        self.assertEqual(store.read_task("conversation-1", "legacy-task"), recovered)





    def test_natural_cancel_resolves_only_one_live_task_in_authenticated_session(self) -> None:
        from types import SimpleNamespace
        store = _test_store(self.root / "natural-cancel.sqlite3")
        host = self.make_host(store=store)
        identities = [
            EvolutionIdentity("conversation-1", "task-one", "attempt-one", "old-run", 2),
            EvolutionIdentity("conversation-foreign", "task-foreign", "attempt-foreign", "old", 1),
        ]
        for identity in identities:
            store.create_task(identity, EvolutionSnapshot(identity, "preparing", 1))
            host._tasks[identity.task_id] = _TaskState(
                identity.task_id, (identity.session_id, "old-run", 2, "old-request"), {}, None, {},
                identity=identity, cancel_event=threading.Event(), worker_thread=SimpleNamespace(is_alive=lambda: True),
            )

        self.live.run_id = "fresh-human-run"
        self.live.turn_epoch = 9
        self.live.request_id = "00000000-0000-4000-8000-000000000009"
        self.assertEqual(host.resolve_unique_active_task(), ("task-one", "attempt-one"))

        extra = EvolutionIdentity("conversation-1", "task-two", "attempt-two", "old-run", 3)
        store.create_task(extra, EvolutionSnapshot(extra, "preparing", 1))
        host._tasks[extra.task_id] = _TaskState(
            extra.task_id, (extra.session_id, "old-run", 3, "old-request"), {}, None, {},
            identity=extra, cancel_event=threading.Event(), worker_thread=SimpleNamespace(is_alive=lambda: True),
        )
        self.assertIsNone(host.resolve_unique_active_task())

        self.live.conversation_id = "conversation-foreign"
        self.assertEqual(host.resolve_unique_active_task(), ("task-foreign", "attempt-foreign"))

if __name__ == "__main__":
    unittest.main()
