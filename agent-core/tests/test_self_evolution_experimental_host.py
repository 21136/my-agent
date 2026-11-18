from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_host import (SelfEvolutionHost, SelfEvolutionHostConfig, TrustedWorkerCapabilities,
                                 _TaskState, _static_ast_evidence, _validate_apply_path_proposal,
                                 _static_ast_write_path_allowed)  # noqa: E402
from self_evolution_snapshot_review import FrozenBaseline, FrozenFile
from self_evolution_types import EvolutionIdentity, EvolutionSnapshot, VerificationEvidence
from self_evolution_store import _snapshot_dict, _snapshot_from  # noqa: E402
from self_evolution_worker import HostTool, SelfEvolutionWorkerContext, run_self_evolution_worker
from self_evolution_verification import VerificationRunner  # noqa: E402


class _Client:
    def __init__(self, calls):
        self.calls = iter(calls)

    def set_cancel_event(self, _event):
        pass

    def chat_confirmed_tools(self, _messages, **kwargs):
        kwargs["on_request_attempt"]()
        kwargs["confirm_fn"]("fixed preview", False)
        kwargs["after_each"]()
        call = next(self.calls)
        return type("Response", (), {
            "tool_calls": [call] if call else [],
            "finish_reason": "tool_calls" if call else "stop",
            "content": "done",
            "raw": {"choices": [{"message": {"tool_calls": [call] if call else []}}]},
        })()


def _call(name, args):
    return {"id": f"call-{name}-{id(args)}", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args),
    }}


def _worker_context():
    return SelfEvolutionWorkerContext(
        session_id="session", task_id="task", attempt_id="attempt", run_id="run",
        turn_epoch=1, candidate_id="candidate", candidate_digest="d" * 64,
        cancel_event=threading.Event(), model_id="trusted-model",
        outbound_request_confirm_fn=lambda _preview, _all: "y",
        current_candidate_digest=lambda: "d" * 64,
        after_each=lambda: True,
    )


def _tree_entry(path, digest, file_id):
    return type("Entry", (), {
        "path": path, "kind": "file", "sha256": digest, "device": 1,
        "file_id": file_id, "mode": 0o644, "size": 6, "link_count": 1,
    })()


def _baseline_manifest():
    return type("Manifest", (), {
        "root_identity": (1, 2), "digest": "b" * 64,
        "entries": (_tree_entry("src.py", "a" * 64, 10),),
        "full_candidate_coverage": True, "excluded_metadata": (),
    })()


def _static_result(_context, args):
    return {
        "status": "passed", "check_id": args["check_id"], "candidate_digest": "d" * 64,
        "baseline_digest": "b" * 64, "before_candidate_digest": "d" * 64,
        "after_candidate_digest": "d" * 64, "verification_type": "static_ast",
        "evidence_scope": "full_candidate_point_in_time", "evidence_ref": "evidence-handle",
        "process_tree_state": "not_applicable", "requires_reconciliation": False,
    }


class ExperimentalStaticAstEvidenceTests(unittest.TestCase):
    def test_static_ast_profile_is_explicit_and_rejects_arbitrary_tools(self):
        client = _Client([])
        caps = TrustedWorkerCapabilities(
            client=client, model_id="model", outbound_request_confirm_fn=lambda *_: "y",
            tools=(), process_tree_stopped=lambda _identity: True,
            enable_experimental_start=True, experimental_profile="static_ast",
        )
        self.assertEqual(caps.experimental_profile, "static_ast")
        with self.assertRaises(ValueError):
            TrustedWorkerCapabilities(
                client=client, model_id="model", outbound_request_confirm_fn=lambda *_: "y",
                tools=(HostTool("observe_candidate", "observe", lambda *_: {"status": "observed"}),),
                process_tree_stopped=lambda _identity: True,
                enable_experimental_start=True, experimental_profile="static_ast",
            )

    def test_static_ast_profile_needs_no_fake_command_or_process_callback(self):
        caps = TrustedWorkerCapabilities(
            client=_Client([]), model_id="model", outbound_request_confirm_fn=None,
            tools=(), process_tree_stopped=None, enable_experimental_start=True,
            experimental_profile="static_ast",
        )
        config = SelfEvolutionHostConfig(
            stable_repository=Path("C:/repo"), trust_root=Path("C:/private/candidates"),
            attribution_paths=("src.py",), verification_source_paths=("src.py",),
            verification_commands={},
        )
        self.assertEqual(dict(config.verification_commands), {})
        self.assertIsNone(caps.process_tree_stopped)
        runner = VerificationRunner({}, workspace_resolver=lambda _task: None)
        self.assertIsNotNone(runner)
        from self_evolution_candidate_tools import build_self_evolution_candidate_tools
        filesystem_tools = build_self_evolution_candidate_tools(
            resolve_candidate_root=lambda _context: None,
            validate_binding=lambda _context, _root: False,
            verification_runner=runner,
            registered_check_ids=(),
            include_verification=False,
        )
        self.assertEqual({tool.name for tool in filesystem_tools}, {
            "observe_candidate", "read_candidate_file", "write_candidate_file",
        })

    def test_worker_preserves_static_ast_evidence_only_when_host_profile_allows_it(self):
        tools = (
            HostTool("observe_candidate", "observe", lambda *_: {"status": "observed"}),
            HostTool("write_candidate_file", "write", lambda *_: {"status": "written"}),
            HostTool("run_registered_check", "check", _static_result),
        )

        def run(allow):
            return run_self_evolution_worker(
                "task", client=_Client([
                    _call("observe_candidate", {}),
                    _call("write_candidate_file", {"path": "src.py", "content": "x = 2\n"}),
                    _call("run_registered_check", {"check_id": "syntax"}),
                    None,
                ]), context=_worker_context(), tools=tools, max_requests=8,
                experimental_profile="static_ast" if allow else "disabled",
            )

        accepted = run(True)
        self.assertEqual(accepted.status, "finished")
        self.assertEqual(accepted.check_evidence[0]["verification_type"], "static_ast")
        self.assertEqual(accepted.check_evidence[0]["process_tree_state"], "not_applicable")
        rejected = run(False)
        self.assertNotEqual(rejected.status, "finished")

    def test_static_ast_check_requires_allowlisted_python_changes_and_stable_full_digest(self):
        baseline = FrozenBaseline(
            candidate_id="candidate", root="C:/candidate", root_identity=(1, 2),
            baseline_digest="b" * 64,
            files=(FrozenFile("src.py", True, "a" * 64, (1, 10), 0o644, b"x = 1\n"),),
            full_tree=True, _seal="test-seal",
        )
        before = type("Manifest", (), {
            "root_identity": (1, 2), "digest": "d" * 64,
            "entries": (_tree_entry("src.py", "c" * 64, 11),),
            "full_candidate_coverage": True, "excluded_metadata": (),
        })()
        after = type("Manifest", (), {
            "root_identity": (1, 2), "digest": "d" * 64,
            "entries": before.entries, "full_candidate_coverage": True, "excluded_metadata": (),
        })()
        with patch("self_evolution_host.snapshot_review._validate_baseline"), \
             patch("self_evolution_host.snapshot_review._capture", side_effect=(before, after)), \
             patch("self_evolution_host.snapshot_review._collect", return_value=(
                 FrozenFile("src.py", True, "c" * 64, (1, 11), 0o644, b"x = 2\n"),
             )):
            evidence = _static_ast_evidence(
                baseline, candidate_id="candidate", allowed_paths=("src.py",),
                expected_candidate_digest="d" * 64, baseline_manifest=_baseline_manifest(),
            )
        self.assertEqual(evidence["verification_type"], "static_ast")
        self.assertEqual(evidence["before_candidate_digest"], evidence["after_candidate_digest"])
        self.assertEqual(evidence["baseline_digest"], "b" * 64)

        with patch("self_evolution_host.snapshot_review._validate_baseline"), \
             patch("self_evolution_host.snapshot_review._capture", side_effect=(before, after)), \
             patch("self_evolution_host.snapshot_review._collect", return_value=(
                 FrozenFile("src.py", True, "c" * 64, (1, 11), 0o644, b"if :\n"),
             )):
            invalid = _static_ast_evidence(
                baseline, candidate_id="candidate", allowed_paths=("src.py",),
                expected_candidate_digest="d" * 64, baseline_manifest=_baseline_manifest(),
            )
            self.assertEqual(invalid["status"], "failed")


    def test_apply_target_readiness_uses_disposable_target_validator(self):
        caps = TrustedWorkerCapabilities(
            client=_Client([]), model_id="model", outbound_request_confirm_fn=None,
            tools=(), process_tree_stopped=None, enable_experimental_start=True,
            experimental_profile="static_ast",
        )
        host = object.__new__(SelfEvolutionHost)
        host._worker_capabilities = caps
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stable = root / "experiment-repo"
            application = root / "application"
            storage = root / "data" / "self-evolution"
            trust = root / "data" / "candidates"
            for path in (stable, application, storage, trust):
                path.mkdir(parents=True)
            host._config = SimpleNamespace(stable_repository=stable, trust_root=trust)
            host._paths = SimpleNamespace(agent_root=application)
            host._store = SimpleNamespace(path=storage / "self-evolution.sqlite3")
            with patch("self_evolution_backend._validate_disposable_target") as validate:
                self.assertTrue(SelfEvolutionHost.experimental_apply_target_ready.fget(host))
            self.assertEqual(validate.call_count, 1)
            args = validate.call_args.args
            self.assertEqual(args, (stable.resolve(), application.resolve(), storage.resolve(), trust.resolve()))

    def test_per_start_approval_is_bound_to_assigned_identity_and_never_allows_allow_all(self):
        identity = EvolutionIdentity("session", "task", "attempt", "run", 3)
        seen = []
        host = object.__new__(SelfEvolutionHost)
        host._worker_binding_valid = lambda _state: True
        state = _TaskState(
            task_id="task", context_identity=("session", "run", 3, "request"),
            workspace_record={}, attribution_baseline=None, created_evidence={},
            identity=identity,
            per_start_outbound_approval_fn=lambda assigned, preview: seen.append((assigned, preview)) or True,
        )
        confirm = SelfEvolutionHost._per_start_outbound_confirm(host, state)
        self.assertEqual(confirm("request preview", False), "y")
        self.assertEqual(seen, [(identity, "request preview")])
        self.assertEqual(confirm("request preview", True), "n")
        self.assertEqual(len(seen), 1)

    def test_out_of_allowlist_write_is_rejected_before_candidate_tool_invocation(self):
        caps = TrustedWorkerCapabilities(
            client=_Client([]), model_id="model", outbound_request_confirm_fn=None,
            tools=(), process_tree_stopped=None, enable_experimental_start=True,
            experimental_profile="static_ast",
        )
        host = object.__new__(SelfEvolutionHost)
        host._worker_capabilities = caps
        host._config = SimpleNamespace(
            attribution_paths=("agent-core/allowed.py",),
            verification_source_paths=("agent-core/allowed.py",),
        )
        host._store = None
        host._worker_binding_valid = lambda _state: True
        context = _worker_context()
        state = _TaskState(
            task_id="task", context_identity=("session", "run", 1, "request"),
            workspace_record={}, attribution_baseline=None, created_evidence={},
            identity=EvolutionIdentity("session", "task", "attempt", "run", 1),
        )
        invoked = []
        tool = HostTool("write_candidate_file", "write", lambda *_: invoked.append(True) or {"status": "written"})
        result = SelfEvolutionHost._invoke_bound_tool(
            host, state, context, tool, {"path": "desktop/other.py", "content": "x = 1"},
        )
        self.assertEqual(result["error_code"], "candidate_write_not_allowlisted")
        self.assertEqual(invoked, [])

    def test_static_write_allowlist_is_intersection_of_fixed_attributions_and_python_sources(self):
        self.assertTrue(_static_ast_write_path_allowed("agent-core/allowed.py", ("agent-core/allowed.py",), ("agent-core/allowed.py",)))
        self.assertFalse(_static_ast_write_path_allowed("desktop/other.py", ("agent-core/allowed.py",), ("agent-core/allowed.py",)))
        self.assertFalse(_static_ast_write_path_allowed("agent-core/extra.py", ("agent-core/allowed.py",), ("agent-core/allowed.py",)))
        config = SelfEvolutionHostConfig(
            stable_repository=Path("C:/repo"), trust_root=Path("C:/private/candidates"),
            attribution_paths=("docs/guide.md",), verification_source_paths=("agent-core/src.py",),
            verification_commands={},
        )
        with tempfile.TemporaryDirectory() as temp:
            from paths import AgentPaths
            app_root = Path(temp) / "app"
            (app_root / "evolve").mkdir(parents=True)
            (app_root / "evolve" / "_index.core.toml").write_text("", encoding="utf-8")
            app_paths = AgentPaths.from_root(app_root)
            caps = TrustedWorkerCapabilities(
                client=_Client([]), model_id="model", outbound_request_confirm_fn=None,
                tools=(), process_tree_stopped=None, enable_experimental_start=True,
                experimental_profile="static_ast",
            )
            with self.assertRaisesRegex(ValueError, "fixed attribution allowlist"):
                SelfEvolutionHost(
                    config, integrity_key=b"k" * 32, context_provider=lambda: None,
                    trusted_verifier_resolver=None, worker_capabilities=caps, paths=app_paths,
                )

    def test_apply_path_proposal_is_canonical_and_exactly_within_disclosed_paths(self):
        self.assertEqual(
            _validate_apply_path_proposal(["b.py", "a.py"], ("a.py", "b.py", "c.py")),
            ("a.py", "b.py"),
        )
        for proposal in (None, (), ["a.py", "a.py"], ["secret.py"], ["../escape.py"]):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                _validate_apply_path_proposal(proposal, ("a.py", "b.py"))

    def test_worker_cannot_publish_awaiting_review_before_host_evidence_settlement(self):
        class Store:
            def __init__(self):
                self.commits = []

            def read_task(self, *_args):
                raise AssertionError("preliminary review phase must not touch durable state")

        host = object.__new__(SelfEvolutionHost)
        store = Store()
        host._store = store
        state = _TaskState(
            task_id="task", context_identity=("session", "run", 1, "request"),
            workspace_record={}, attribution_baseline=None, created_evidence={},
        )
        SelfEvolutionHost._persist_worker_phase(host, state, "awaiting_review")
        self.assertEqual(store.commits, [])

    def test_host_provenance_accepts_static_only_for_exact_profile_and_full_digest_pair(self):
        caps = TrustedWorkerCapabilities(
            client=_Client([]), model_id="model", outbound_request_confirm_fn=None,
            tools=(), process_tree_stopped=None, enable_experimental_start=True,
            experimental_profile="static_ast",
        )
        host = object.__new__(SelfEvolutionHost)
        host._worker_capabilities = caps
        host._current_candidate_digest = lambda _state: "d" * 64
        baseline = FrozenBaseline(
            candidate_id="candidate", root="C:/candidate", root_identity=(1, 2),
            baseline_digest="b" * 64, files=(), full_tree=True, _seal="test-seal",
        )
        state = _TaskState(
            task_id="task", context_identity=("session", "run", 1, "request"),
            workspace_record={}, attribution_baseline=None, created_evidence={},
            snapshot_review_baseline=baseline,
            identity=EvolutionIdentity("session", "task", "attempt", "run", 1),
        )
        result = {
            "check_id": "syntax", "status": "passed", "candidate_digest": "d" * 64,
            "verification_type": "static_ast", "baseline_digest": "b" * 64,
            "before_candidate_digest": "d" * 64, "after_candidate_digest": "d" * 64,
            "process_tree_state": "not_applicable", "requires_reconciliation": False,
            "exit_code": None, "evidence_scope": "full_candidate_point_in_time",
        }
        SelfEvolutionHost._record_check_evidence_provenance(host, state, {"check_id": "syntax"}, result)
        self.assertEqual(state.check_evidence_provenance["syntax"]["verification_type"], "static_ast")
        state.check_evidence_provenance.clear()
        mismatched = {**result, "after_candidate_digest": "e" * 64}
        SelfEvolutionHost._record_check_evidence_provenance(host, state, {"check_id": "syntax"}, mismatched)
        self.assertEqual(state.check_evidence_provenance, {})

    def test_static_evidence_type_requires_matching_full_digests_and_is_serializable(self):
        evidence = VerificationEvidence(
            check_id="static_ast", candidate_digest="d" * 64, status="passed",
            exit_code=None, process_tree_state="not_applicable", evidence_ref="opaque-ref",
            verification_type="static_ast", baseline_digest="b" * 64,
            before_candidate_digest="d" * 64, after_candidate_digest="d" * 64,
        )
        self.assertEqual(evidence.process_tree_state, "not_applicable")
        snapshot = EvolutionSnapshot(
            identity=EvolutionIdentity("session", "task", "attempt", "run", 1),
            phase="awaiting_review", snapshot_revision=2, candidate_digest="d" * 64,
            evidence=(evidence,),
        )
        restored = _snapshot_from(_snapshot_dict(snapshot))
        self.assertEqual(restored.evidence[0].verification_type, "static_ast")
        self.assertEqual(restored.evidence[0].process_tree_state, "not_applicable")
        with self.assertRaises(ValueError):
            VerificationEvidence(
                check_id="static_ast", candidate_digest="d" * 64, status="passed",
                exit_code=0, process_tree_state="not_applicable", evidence_ref="opaque-ref",
                verification_type="static_ast", baseline_digest="b" * 64,
                before_candidate_digest="d" * 64, after_candidate_digest="e" * 64,
            )
        with self.assertRaises(ValueError):
            VerificationEvidence(
                check_id="execution", candidate_digest="d" * 64, status="passed",
                exit_code=0, process_tree_state="not_applicable", evidence_ref="opaque-ref",
            )

    def test_static_ast_check_fails_closed_for_non_allowlisted_change(self):
        baseline = FrozenBaseline(
            candidate_id="candidate", root="C:/candidate", root_identity=(1, 2),
            baseline_digest="b" * 64,
            files=(FrozenFile("src.py", True, "a" * 64, (1, 10), 0o644, b"x = 1\n"),),
            full_tree=True, _seal="test-seal",
        )
        manifest = type("Manifest", (), {
            "root_identity": (1, 2), "digest": "d" * 64,
            "entries": (_tree_entry("src.py", "a" * 64, 10),
                        _tree_entry("other.py", "c" * 64, 12)),
            "full_candidate_coverage": True, "excluded_metadata": (),
        })()
        with patch("self_evolution_host.snapshot_review._validate_baseline"), \
             patch("self_evolution_host.snapshot_review._capture", return_value=manifest):
            out_of_scope = _static_ast_evidence(
                baseline, candidate_id="candidate", allowed_paths=("src.py",),
                expected_candidate_digest="d" * 64, baseline_manifest=_baseline_manifest(),
            )
            self.assertEqual(out_of_scope["status"], "failed")


if __name__ == "__main__":
    unittest.main()
