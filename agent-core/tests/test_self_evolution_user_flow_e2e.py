from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_candidate_tools import build_self_evolution_candidate_tools
from self_evolution_host import (
    HostRunContext,
    SelfEvolutionHost,
    SelfEvolutionHostConfig,
)
from self_evolution_merge_integration import NativeClaim, prepare_host_selected_apply
from self_evolution_snapshot_review import freeze_baseline, review_candidate
from self_evolution_verification import VerificationRunner, WorkspaceBinding
from self_evolution_worker import SelfEvolutionWorkerContext, run_self_evolution_worker


class _LiveRun:
    def __init__(self) -> None:
        self.conversation_id = "e2e-session"
        self.run_id = "e2e-run"
        self.turn_epoch = 1
        self.request_id = "00000000-0000-4000-8000-000000000011"
        self.cancel_event = threading.Event()

    def snapshot(self) -> HostRunContext:
        return HostRunContext(
            conversation_id=self.conversation_id, run_id=self.run_id, turn_epoch=self.turn_epoch,
            mode="agent", work_intent="execute", cancel_event=self.cancel_event,
            request_id=self.request_id, request_origin="human", active=True,
            self_evolution_intent=True,
        )


class _ScriptedModel:
    """Deterministic fake provider; confirmed-tools boundary and worker loop stay real."""

    def __init__(self, turns: list[tuple[str, dict] | None], final: str) -> None:
        self.turns = list(turns)
        self.final = final
        self.calls = 0
        self.tool_results: list[dict] = []
        self._active_client = None
        self._cancel_event = None

    def set_cancel_event(self, event) -> None:
        self._cancel_event = event

    def chat_confirmed_tools(self, messages, **kwargs):
        self.calls += 1
        if messages and messages[-1].get("role") == "tool":
            self.tool_results.append(json.loads(messages[-1]["content"]))
        kwargs["on_request_attempt"]()
        if kwargs["confirm_fn"]("test-only synthetic model request preview", False) != "y":
            raise AssertionError("fake model request was not approved")
        if kwargs["after_each"]() is not True:
            raise AssertionError("candidate binding did not remain valid")
        if not self.turns:
            return _Response(content=self.final)
        action = self.turns.pop(0)
        if action is None:
            return _Response(content=self.final)
        name, arguments = action
        return _Response(calls=[_tool_call(name, arguments)])


class _Response:
    def __init__(self, *, content: str | None = None, calls: list[dict] | None = None) -> None:
        self.content = content
        self.tool_calls = calls or []
        self.finish_reason = "tool_calls" if calls else "stop"
        self.raw = {"choices": [{"message": {"tool_calls": self.tool_calls}}]}


def _tool_call(name: str, arguments: dict) -> dict:
    return {"id": f"call-{name}-{hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:10]}",
            "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def _chunk(write_id: str, index: int, data: bytes, full: bytes) -> tuple[str, dict]:
    envelope = {"$candidate_write_chunk": 1, "write_id": write_id, "index": index,
                "total_bytes": len(full), "sha256": hashlib.sha256(full).hexdigest(),
                "data": base64.b64encode(data).decode("ascii")}
    return "write_candidate_file", {"path": "agent-core/tracked.py", "content": json.dumps(envelope)}


class SelfEvolutionUserFlowE2ETests(unittest.TestCase):
    """Non-production composition tests with isolated repositories and explicit seams."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="self-evolution-user-flow-e2e-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stable = self.root / "stable"
        self.trust_root = self.root / "candidates"
        self.stable.mkdir()
        self.trust_root.mkdir()
        (self.stable / "agent-core").mkdir()
        self.git("init", "--quiet", str(self.stable))
        self.git("-C", str(self.stable), "config", "user.name", "E2E Fixture")
        self.git("-C", str(self.stable), "config", "user.email", "e2e@example.invalid")
        (self.stable / "agent-core/tracked.py").write_bytes(b"VALUE = 1\n")
        (self.stable / "agent-core/other.py").write_bytes(b"OTHER = 1\n")
        self.git("-C", str(self.stable), "add", "agent-core/tracked.py", "agent-core/other.py")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "isolated baseline")
        self.live = _LiveRun()
        self.key = b"isolated-e2e-host-integrity-key-32-bytes-minimum"
        check = (
            sys.executable, "-c",
            "import ast,pathlib; t=ast.parse(pathlib.Path('agent-core/tracked.py').read_text()); "
            "assert ast.literal_eval(t.body[0].value)==2",
        )
        self.config = SelfEvolutionHostConfig(
            stable_repository=self.stable, trust_root=self.trust_root,
            attribution_paths=("agent-core/tracked.py", "agent-core/other.py"),
            verification_source_paths=("agent-core/tracked.py", "agent-core/other.py"),
            verification_commands={"syntax-and-value": check}, verification_timeout_seconds=10,
        )
        self.host = SelfEvolutionHost(
            self.config, integrity_key=self.key, context_provider=self.live.snapshot,
            trusted_verifier_resolver=lambda *_args: None,
        )
        self.assertFalse(self.host.production_ready)
        self.assertFalse(self.host.full_candidate_coverage_available)
        self.assertFalse(self.host.worker_bound)
        self.assertFalse(self.host.merge_authorized)

    @staticmethod
    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return result.stdout.strip()

    def _create_host_candidate(self, *, freeze: bool = True) -> tuple[str, Path, object]:
        # Exercise the unmodified Host entry point first. Its real candidate gate is closed,
        # so the controlled worker fixture is exported from a temporary Git commit below.
        self.assertFalse(self.host.production_ready)
        self.assertFalse(self.host.full_candidate_coverage_available)
        self.assertFalse(self.host.worker_bound)
        self.assertFalse(self.host.merge_authorized)
        create_result = self.host.create()
        self.assertEqual(create_result["status"], "failed", create_result)
        self.assertEqual(create_result["error_code"], "safe_checkout_unavailable")
        task_id, candidate_id = "e2e-task", "e2e-candidate"
        candidate = self.trust_root / candidate_id
        candidate.mkdir()
        for relative in ("agent-core/tracked.py", "agent-core/other.py"):
            target = candidate / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.run(
                ["git", "-C", str(self.stable), "show", f"HEAD:{relative}"],
                check=True, capture_output=True,
            ).stdout)
        baseline = freeze_baseline(candidate_root=candidate, candidate_id=candidate_id) if freeze else None
        return task_id, candidate, (candidate_id, baseline)

    @staticmethod
    def _candidate_digest(candidate: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in candidate.rglob("*") if item.is_file()):
            relative = path.relative_to(candidate).as_posix().encode("utf-8")
            content = path.read_bytes()
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    def _run_real_worker(self, task_id: str, candidate: Path, candidate_id: str,
                         model: _ScriptedModel):
        def resolve(context):
            return str(candidate) if (context.task_id == task_id and context.candidate_id == candidate_id) else None

        def validate(context, root):
            try:
                return (context.task_id == task_id and context.candidate_id == candidate_id
                        and Path(root).resolve(strict=True) == candidate.resolve(strict=True)
                        and candidate.resolve(strict=True) != self.stable.resolve(strict=True))
            except (OSError, RuntimeError):
                return False

        binding = WorkspaceBinding(
            task_id, candidate, candidate_id, "isolated-test-config",
            self.config.verification_source_paths,
        )
        runner = VerificationRunner(
            self.config.verification_commands,
            workspace_resolver=lambda requested: binding if requested == task_id else None,
        )
        tools = build_self_evolution_candidate_tools(
            resolve_candidate_root=resolve, validate_binding=validate,
            verification_runner=runner, registered_check_ids=("syntax-and-value",),
            check_timeout_seconds=10,
        )
        context = SelfEvolutionWorkerContext(
            session_id=self.live.conversation_id, task_id=task_id, attempt_id="isolated-test-attempt",
            run_id=self.live.run_id, turn_epoch=self.live.turn_epoch, candidate_id=candidate_id,
            candidate_digest=self._candidate_digest(candidate), cancel_event=threading.Event(),
            model_id="injected-fake-model", outbound_request_confirm_fn=lambda _preview, _allow_all: "y",
            after_each=lambda: validate(SimpleNamespace(task_id=task_id, candidate_id=candidate_id), candidate),
            current_candidate_digest=lambda: self._candidate_digest(candidate),
        )
        result = run_self_evolution_worker(
            "Update agent-core/tracked.py as requested; inspect it, edit it, and run syntax-and-value.",
            client=model, context=context, tools=tools, max_requests=16,
        )
        return result, tools

    @unittest.skipUnless(os.name == "nt", "full-tree snapshot review currently requires Windows held handles")
    def test_observe_read_chunked_edit_check_review_explicit_selected_apply(self) -> None:
        task_id, candidate, bound = self._create_host_candidate()
        candidate_id, baseline = bound
        desired = b"VALUE = 2\n"
        midpoint = len(desired) // 2
        model = _ScriptedModel([
            ("observe_candidate", {}),
            ("read_candidate_file", {"path": "agent-core/tracked.py"}),
            _chunk("complete-write", 0, desired[:midpoint], desired),
            _chunk("complete-write", 1, desired[midpoint:], desired),
            ("write_candidate_file", {"path": "agent-core/other.py", "content": "OTHER = 2\n"}),
            ("run_registered_check", {"check_id": "syntax-and-value"}),
            None,
        ], "Candidate updated and registered check passed.")

        result, tools = self._run_real_worker(task_id, candidate, candidate_id, model)
        # The real registered command and its contained Job process tree both completed.
        self.assertEqual(result.status, "finished", result)
        self.assertEqual(result.phase, "awaiting_review")
        self.assertEqual((candidate / "agent-core/tracked.py").read_bytes(), desired)
        self.assertEqual((candidate / "agent-core/other.py").read_text(encoding="utf-8"), "OTHER = 2\n")
        self.assertEqual(result.check_status, "passed")
        self.assertEqual(result.check_evidence[0]["check_id"], "syntax-and-value")
        self.assertEqual(result.check_evidence[0]["exit_code"], 0)
        self.assertEqual(result.check_evidence[0]["process_tree_state"], "stopped")
        self.assertEqual(model.calls, 7)
        self.assertEqual(model.tool_results[0]["status"], "observed")
        self.assertIn("agent-core/tracked.py", {item["path"] for item in model.tool_results[0]["files"]})
        self.assertEqual(model.tool_results[1]["status"], "ok")
        self.assertEqual(model.tool_results[1]["content"], "VALUE = 1\n")

        observed = tools[0].invoke(
            SelfEvolutionWorkerContext(
                session_id=self.live.conversation_id, task_id=task_id, attempt_id="review-attempt",
                run_id=self.live.run_id, turn_epoch=self.live.turn_epoch, candidate_id=candidate_id,
                candidate_digest="review", cancel_event=threading.Event(), model_id="injected-fake-model",
                outbound_request_confirm_fn=lambda *_: "y", after_each=lambda: True,
                current_candidate_digest=lambda: "review",
            ), {},
        )
        self.assertEqual(observed["status"], "observed")
        self.assertIn("agent-core/tracked.py", {entry["path"] for entry in observed["files"]})

        # The complete metadata/content diff is reviewed against the pre-worker frozen tree.
        reviewed = review_candidate(baseline, candidate_id=candidate_id, disclose_paths=("agent-core/tracked.py", "agent-core/other.py"))
        self.assertTrue(reviewed["full_candidate_coverage"])
        changed = {item["path"] for item in reviewed["files"]}
        self.assertEqual(changed, {"agent-core/tracked.py", "agent-core/other.py"})
        tracked_diff = next(item["diff"] for item in reviewed["files"]
                            if item["path"] == "agent-core/tracked.py")
        self.assertIn("-VALUE = 1", tracked_diff)
        self.assertIn("+VALUE = 2", tracked_diff)

        prepared = prepare_host_selected_apply(
            repository_root=self.stable, baseline=baseline,
            session_id=self.live.conversation_id, task_id=task_id, attempt_id="isolated-test-attempt",
            candidate_id=candidate_id, selected_paths=("agent-core/tracked.py",),
            trusted_host_binding_is_current=lambda: True,
        )
        preview = prepared.preview()
        # This typed Host claim is a test-only explicit-confirmation stand-in, not native UI.
        confirmed_requests = []
        def confirm_exact_request(request):
            confirmed_requests.append(request)
            return NativeClaim(
                approved=True, request_sha256=request["request_sha256"],
                operation_id=request["operation_id"], selected_paths=request["selected_paths"],
            )
        applied = prepared.apply(trusted_native_claim=confirm_exact_request)
        self.assertEqual(applied.status, "applied", applied)
        self.assertEqual(applied.written_paths, ("agent-core/tracked.py",))
        self.assertEqual((self.stable / "agent-core/tracked.py").read_bytes(), desired)
        self.assertEqual((self.stable / "agent-core/other.py").read_text(encoding="utf-8"), "OTHER = 1\n")
        self.assertEqual([row["path"] for row in preview["files"]], ["agent-core/tracked.py"])
        self.assertEqual(len(confirmed_requests), 1)
        self.assertEqual(confirmed_requests[0]["request_sha256"], preview["request_sha256"])
        self.assertFalse(self.host.merge_authorized)

    def test_intermediate_chunk_acceptance_is_not_a_completed_edit(self) -> None:
        task_id, candidate, bound = self._create_host_candidate(freeze=False)
        candidate_id, _baseline = bound
        original = (candidate / "agent-core/tracked.py").read_bytes()
        proposed = b"VALUE = 2\n"
        client = _ScriptedModel([
            ("observe_candidate", {}),
            ("read_candidate_file", {"path": "agent-core/tracked.py"}),
            _chunk("incomplete-write", 0, proposed[:3], proposed),
            ("run_registered_check", {"check_id": "syntax-and-value"}),
            None,
        ], "I changed agent-core/tracked.py and the check passed.")

        result, _tools = self._run_real_worker(task_id, candidate, candidate_id, client)
        # The fake model captured the response from the actual candidate write tool.
        self.assertEqual(client.tool_results[2]["status"], "ok")
        self.assertEqual(client.tool_results[2]["chunk_status"], "accepted")
        self.assertEqual((candidate / "agent-core/tracked.py").read_bytes(), original)

        # Regression: an accepted intermediate chunk is pending data, not an edit. The
        # worker must refuse verification and final success while the file remains unchanged.
        self.assertEqual(result.status, "blocked", result)
        self.assertEqual(result.error_code, "candidate_write_incomplete")
        self.assertEqual(result.phase, "editing")
        self.assertEqual(result.check_status, "not_run")
        self.assertEqual((candidate / "agent-core/tracked.py").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
