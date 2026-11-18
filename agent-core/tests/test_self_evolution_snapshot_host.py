from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from self_evolution_host import (HostRunContext, HostRunContextProvider, ReviewDisclosureDecision, SelfEvolutionHost, SelfEvolutionHostConfig)  # noqa: E402
from self_evolution_materializer import MaterializationResult  # noqa: E402
from self_evolution_verification import WorkspaceBinding  # noqa: E402
from self_evolution_types import EvolutionIdentity  # noqa: E402


class _Live:
    def __call__(self):
        return HostRunContext(
            conversation_id="snapshot-host-test", run_id="run-1", turn_epoch=1,
            mode="agent", work_intent="execute", cancel_event=threading.Event(),
            request_id="00000000-0000-4000-8000-000000000401",
            request_origin="human", active=True, self_evolution_intent=True,
        )


class _FixtureTrustedSnapshotMaterializer:
    """Test-only materializer that publishes real fixture files and an HMAC binding."""
    max_object_store_bytes = 1
    max_object_bytes = 1
    max_total_bytes = 1
    max_tree_bytes = 1
    max_files = 1
    max_object_files = 1
    timeout_seconds = 1

    def __init__(self, git_executable, **_limits):
        self.git_executable = git_executable

    @staticmethod
    def _directory_identity(path):
        from self_evolution_materializer import SafeSnapshotMaterializer
        return SafeSnapshotMaterializer._directory_identity(Path(path))

    @staticmethod
    def _is_reparse(info):
        # Workspace anchor validation calls this on the exact materializer
        # class. Delegate to the production check; do not bypass anchor safety.
        from self_evolution_materializer import SafeSnapshotMaterializer
        return SafeSnapshotMaterializer._is_reparse(info)

    @staticmethod
    def _candidate_path_digest(path):
        normalized = os.path.normcase(os.path.abspath(os.fspath(path)))
        return hashlib.sha256(os.fsencode(normalized)).hexdigest()

    def materialize_bound(self, object_directory, commit_oid, destination, *, candidate_id, integrity_key):
        stable = Path(object_directory).parent.parent
        output = Path(destination)
        output.mkdir()
        # Deliberately materialize a real file tree, not a fake Git worktree.
        (output / "tracked.txt").write_bytes((stable / "tracked.txt").read_bytes())
        (output / "fixture-video.mp4").write_bytes((stable / "fixture-video.mp4").read_bytes())
        if (stable / "agent-core" / "test.py").exists():
            (output / "agent-core").mkdir()
            shutil.copyfile(stable / "agent-core" / "test.py", output / "agent-core" / "test.py")
        tree_oid = subprocess.run(
            ["git", "-C", str(stable), "rev-parse", f"{commit_oid}^{{tree}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        manifest_digest = hashlib.sha256(
            (candidate_id + commit_oid + tree_oid
             + hashlib.sha256((output / "tracked.txt").read_bytes()).hexdigest()
             + hashlib.sha256((output / "fixture-video.mp4").read_bytes()).hexdigest()).encode()
        ).hexdigest()
        record = {
            "schema": "self-evolution-candidate-binding.v1",
            "candidate_id": candidate_id,
            "candidate_path_digest": self._candidate_path_digest(output),
            "authorized_parent_identity": list(self._directory_identity(output.parent)),
            "commit_oid": commit_oid.lower(),
            "tree_oid": tree_oid.lower(),
            "manifest_digest": manifest_digest,
            "directory_identity": list(self._directory_identity(output)),
            "candidate_kind": "materialized_snapshot",
            "snapshot_scope": "committed_tree_only",
            "os_sandbox": False,
        }
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        envelope = {"record": record, "hmac_sha256": hmac.new(integrity_key, canonical, hashlib.sha256).hexdigest()}
        (output / ".self-evolution-binding.json").write_text(json.dumps(envelope), encoding="utf-8")
        return MaterializationResult(
            commit_oid=commit_oid.lower(), destination=str(output), file_count=2,
            total_bytes=sum((output / name).stat().st_size for name in ("tracked.txt", "fixture-video.mp4")), object_count=2,
            object_store_bytes=1, candidate_kind="materialized_snapshot", tree_oid=tree_oid.lower(),
            manifest_digest=manifest_digest,
        )

    def read_binding(self, destination, *, expected_candidate_id, integrity_key, expected_parent_identity=None):
        envelope = json.loads((Path(destination) / ".self-evolution-binding.json").read_text(encoding="utf-8"))
        record = envelope["record"]
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        expected = hmac.new(integrity_key, canonical, hashlib.sha256).hexdigest()
        if (not hmac.compare_digest(envelope.get("hmac_sha256", ""), expected)
                or record.get("candidate_id") != expected_candidate_id
                or record.get("candidate_path_digest") != self._candidate_path_digest(destination)
                or (expected_parent_identity is not None and (
                    record.get("authorized_parent_identity") != list(expected_parent_identity)
                    or self._directory_identity(Path(destination).parent) != expected_parent_identity))
                or record.get("directory_identity") != list(self._directory_identity(destination))):
            raise ValueError("fixture snapshot binding mismatch")
        return record


class _SnapshotTestHost(SelfEvolutionHost):
    def __init__(self, config, *args, **kwargs):
        from self_evolution_workspace import SelfEvolutionWorkspace
        workspace = kwargs.pop("workspace", None)
        if workspace is None:
            key = kwargs.get("integrity_key")
            workspace = SelfEvolutionWorkspace(
                config.stable_repository, config.trust_root, key,
                allow_initial_enrollment=True,
            )
        super().__init__(config, *args, workspace=workspace, **kwargs)

    @property
    def snapshot_candidate_creation_available(self):
        # Test-only opt-in uses a fixture materializer and disposable files.
        return True


class MaterializedSnapshotHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="self-evolution-snapshot-host-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.stable = root / "stable"
        self.trust = root / "candidates"
        self.stable.mkdir()
        self.trust.mkdir()
        self.git("init", "--quiet", str(self.stable))
        self.git("-C", str(self.stable), "config", "user.name", "Snapshot Test")
        self.git("-C", str(self.stable), "config", "user.email", "snapshot@example.invalid")
        self.git("-C", str(self.stable), "config", "core.autocrlf", "false")
        (self.stable / "tracked.txt").write_text("committed source\n", encoding="utf-8")
        (self.stable / "fixture-video.mp4").write_bytes(b"\x00snapshot-video" * (10 * 1024 * 1024 // 15))
        self.git("-C", str(self.stable), "add", "tracked.txt", "fixture-video.mp4")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "fixture")
        self.context = _Live()
        self.config = SelfEvolutionHostConfig(
            stable_repository=self.stable, trust_root=self.trust,
            attribution_paths=("tracked.txt",), verification_source_paths=("tracked.txt",),
            verification_commands={"syntax": (sys.executable, "-c", "print('ok')")},
            verification_timeout_seconds=5,
        )
        self.key = b"snapshot-host-test-key-32-bytes!!"
        self.host = _SnapshotTestHost(
            self.config, integrity_key=self.key, context_provider=self.context,
            trusted_verifier_resolver=lambda task_id, record, config_id, candidate_id: None,
        )

    def git(self, *args):
        result = subprocess.run(["git", *args], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f"fixture git failed: {args!r}: {result.stderr}")
        return result.stdout.strip()

    def test_host_create_and_inspect_bind_a_real_plain_snapshot(self):
        # Swap in a trusted fixture-only materializer which creates a real plain
        # file candidate. No production safety/coverage gate is patched or opened.
        import self_evolution_workspace
        self.host._snapshot_materializer = _FixtureTrustedSnapshotMaterializer(shutil.which("git"))
        materializer_type_patch = mock.patch.object(
            self_evolution_workspace, "SafeSnapshotMaterializer", _FixtureTrustedSnapshotMaterializer,
        )
        self.enterContext(materializer_type_patch)
        created = self.host.create()
        self.assertEqual(created["status"], "created", created)
        self.assertEqual(created["candidate"]["candidate_kind"], "materialized_snapshot")
        self.assertEqual(created["candidate"]["snapshot_scope"], "committed_head_tree_only")
        anchor = self.host._workspace._read_persistent_trust_root_anchor()
        self.assertEqual(anchor["trust_root_directory_identity"], list(self.host._workspace._trust_root_identity))
        self.assertEqual(anchor["trust_root_parent_identity"], list(self.host._workspace._trust_root_parent_identity))
        candidate = self.trust / created["task_id"]
        self.assertTrue((candidate / "tracked.txt").is_file())
        self.assertEqual((candidate / "tracked.txt").read_text(encoding="utf-8"), "committed source\n")
        self.assertTrue((candidate / "fixture-video.mp4").is_file())
        self.assertGreater((candidate / "fixture-video.mp4").stat().st_size, 10 * 1024 * 1024 - 15)
        self.assertFalse((candidate / ".git").exists(), "snapshot must not masquerade as a linked worktree")

        inspected = self.host.inspect(created["task_id"])
        self.assertEqual(inspected["status"], "inspected", inspected)
        self.assertEqual(inspected["candidate"]["candidate_kind"], "materialized_snapshot")
        self.assertFalse(inspected["merge_authorized"])

        (candidate / "tracked.txt").write_text("worker edit\n", encoding="utf-8")
        (candidate / "fixture-video.mp4").write_bytes(b"\x01edited-video" * (10 * 1024 * 1024 // 14))
        inspected_after_edit = self.host.inspect(created["task_id"])
        self.assertEqual(inspected_after_edit["status"], "inspected", inspected_after_edit)
        reviewed = self.host.review_candidate(created["task_id"])
        self.assertEqual(reviewed["status"], "reviewed", reviewed)
        self.assertEqual(reviewed["review"]["scope"], "bounded_full_tree_point_in_time_not_atomic")
        self.assertFalse(reviewed["review"]["snapshot_coverage_complete"])
        self.assertFalse(reviewed["merge_authorized"])
        text_review = next(row for row in reviewed["review"]["files"] if row["path"] == "tracked.txt")
        self.assertEqual(text_review["change"], "modified")
        self.assertNotIn("diff", text_review)
        no_disclosure = self.host.review_candidate_content(created["task_id"])
        self.assertEqual(no_disclosure["status"], "failed")
        self.assertEqual(no_disclosure["error_code"], "content_disclosure_callback_unavailable")
        video_review = next(row for row in reviewed["review"]["files"]
                            if row["path"] == "fixture-video.mp4")
        self.assertEqual(video_review["change"], "modified")
        self.assertTrue(video_review["content_changed"])
        self.assertEqual(video_review["before_content_status"], "over_text_limit")
        self.assertNotIn("diff", video_review)

        self.host._trusted_verifier_resolver = lambda task_id, record, config_id, candidate_id: WorkspaceBinding(
            task_id=task_id, candidate_root=record["candidate_path"], candidate_id=candidate_id,
            config_identity=config_id, source_paths=self.config.verification_source_paths,
        )
        verification = self.host.verify(created["task_id"])
        self.assertIn(verification["status"], {
            "verification_incomplete_coverage", "verification_unknown", "verification_failed",
        }, verification)
        self.assertFalse(verification["verification_passed"])
        self.assertFalse(verification["merge_authorized"])


    def test_inspection_binds_manifest_root_and_rechecks_persisted_binding(self):
        from dataclasses import replace
        import self_evolution_workspace
        import self_evolution_host
        self.host._snapshot_materializer = _FixtureTrustedSnapshotMaterializer(shutil.which("git"))
        with mock.patch.object(self_evolution_workspace, "SafeSnapshotMaterializer", _FixtureTrustedSnapshotMaterializer):
            created = self.host.create()
        self.assertEqual(created["status"], "created", created)
        task_id = created["task_id"]
        capture = self_evolution_host.capture_tree_manifest
        def wrong_root(path):
            tree = capture(path)
            return replace(tree, root_identity=(tree.root_identity[0], tree.root_identity[1] + 1))
        with mock.patch.object(self_evolution_host, "capture_tree_manifest", side_effect=wrong_root):
            result = self.host.inspect(task_id)
        self.assertEqual(result["error_code"], "snapshot_root_identity_mismatch", result)
        read = self.host._snapshot_materializer.read_binding
        calls = []
        def changed_binding(*args, **kwargs):
            result = read(*args, **kwargs)
            calls.append(None)
            if len(calls) == 2:
                result = dict(result, manifest_digest="0" * 64)
            return result
        with mock.patch.object(self.host._snapshot_materializer, "read_binding", side_effect=changed_binding):
            result = self.host.inspect(task_id)
        self.assertEqual(result["error_code"], "snapshot_binding_changed_during_inspection", result)
        self.assertEqual(self.host.inspect(task_id)["status"], "inspected")

    def test_registration_rejects_frozen_baseline_from_a_different_root(self):
        from dataclasses import replace
        import self_evolution_workspace
        import self_evolution_host
        self.host._snapshot_materializer = _FixtureTrustedSnapshotMaterializer(shutil.which("git"))
        freeze = self_evolution_host.snapshot_review.freeze_baseline
        def wrong_root(*args, **kwargs):
            baseline = freeze(*args, **kwargs)
            return replace(baseline, root_identity=(baseline.root_identity[0], baseline.root_identity[1] + 1))
        with mock.patch.object(self_evolution_workspace, "SafeSnapshotMaterializer", _FixtureTrustedSnapshotMaterializer), \
             mock.patch.object(self_evolution_host.snapshot_review, "freeze_baseline", side_effect=wrong_root):
            result = self.host.create()
        self.assertEqual(result["error_code"], "snapshot_root_identity_mismatch", result)
        self.assertEqual(result["publication_status"], "published")
        self.assertTrue((self.trust / result["task_id"]).is_dir())
        self.assertFalse(result["merge_authorized"])

    def test_published_creation_failure_reports_preserved_candidate(self):
        from self_evolution_workspace import WorkspaceError
        task_id = "12345678-1234-4234-8234-123456789abc"
        error = WorkspaceError("published but final check failed", code="concurrent_state", stage="snapshot_final_check",
                               candidate_id=task_id, publication_status="published")
        with mock.patch.object(self.host._workspace, "create_snapshot_candidate", side_effect=error):
            result = self.host.create()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "concurrent_state")
        self.assertEqual(result.get("task_id"), task_id)
        self.assertEqual(result.get("publication_status"), "published")
        self.assertFalse(result["merge_authorized"])
        unknown = WorkspaceError("publication state requires recovery", code="publication_unknown",
                                 stage="snapshot_materialization", candidate_id=task_id,
                                 publication_status="unknown")
        with mock.patch.object(self.host._workspace, "create_snapshot_candidate", side_effect=unknown):
            unresolved = self.host.create()
        self.assertEqual(unresolved["status"], "unknown")
        self.assertEqual(unresolved["publication_status"], "unknown")
        self.assertEqual(unresolved["task_id"], task_id)

    def test_host_content_review_requires_trusted_digest_bound_disclosure_and_rejects_changes(self):
        decisions = []
        mutate_after_confirmation = [False]
        decision_mode = ["approve"]
        candidate_path = self.trust

        def disclosure_callback(request):
            decisions.append(dict(request))
            if mutate_after_confirmation[0]:
                (candidate_path / request["task_id"] / "tracked.txt").write_text(
                    "changed after confirmation\n", encoding="utf-8")
            return ReviewDisclosureDecision(
                approved=decision_mode[0] != "deny",
                request_sha256=("0" * 64 if decision_mode[0] == "wrong_digest"
                                else request["request_sha256"]),
                approved_paths=("not-changed.txt",) if decision_mode[0] == "wrong_path"
                else ("tracked.txt",),
            )

        self.host = _SnapshotTestHost(
            self.config, integrity_key=self.key, context_provider=self.context,
            trusted_verifier_resolver=lambda *_args: None,
            trusted_review_disclosure_fn=disclosure_callback,
        )
        import self_evolution_workspace
        self.host._snapshot_materializer = _FixtureTrustedSnapshotMaterializer(shutil.which("git"))
        self.enterContext(mock.patch.object(
            self_evolution_workspace, "SafeSnapshotMaterializer", _FixtureTrustedSnapshotMaterializer))
        created = self.host.create()
        self.assertEqual(created["status"], "created", created)
        task_id = created["task_id"]
        (self.trust / task_id / "tracked.txt").write_text("candidate edit\n", encoding="utf-8")

        with mock.patch("self_evolution_host.snapshot_review.freeze_baseline",
                        side_effect=AssertionError("review must reuse Host-frozen baseline")):
            reviewed = self.host.review_candidate_content(task_id)
        self.assertEqual(reviewed["status"], "reviewed", reviewed)
        self.assertIn("-committed source", reviewed["review"]["files"][0]["diff"])
        self.assertIn("+candidate edit", reviewed["review"]["files"][0]["diff"])
        self.assertIsNone(decisions[0]["attempt_id"], "create has no worker attempt yet")
        self.assertEqual(decisions[0]["task_id"], task_id)
        self.assertEqual(decisions[0]["candidate_digest"], reviewed["review"]["candidate_digest"])
        self.assertEqual(decisions[0]["approved_scope"], "changed_files_only")
        self.assertEqual(decisions[0]["changed_files"][0]["path"], "tracked.txt")
        self.assertEqual(reviewed["review"]["disclosure_binding"]["request_sha256"],
                         decisions[0]["request_sha256"])
        self.assertEqual(reviewed["review"]["disclosure_binding"]["approved_paths"], ("tracked.txt",))
        self.assertFalse(reviewed["review"]["full_candidate_coverage"])
        self.assertFalse(reviewed["review"]["review_is_atomic"])
        self.assertFalse(reviewed["merge_authorized"])

        with self.assertRaises(TypeError):
            self.host.review_candidate_content(task_id, disclose_paths=("tracked.txt",))

        state = self.host._tasks[task_id]
        identity = EvolutionIdentity("snapshot-host-test", task_id, "attempt-1", "run-1", 1)
        state.identity = identity
        self.host._store = SimpleNamespace(read_task=lambda _session, _task: SimpleNamespace(
            identity=identity, phase="awaiting_review"))
        attempted_review = self.host.review_candidate_content(task_id)
        self.assertEqual(attempted_review["status"], "reviewed", attempted_review)
        self.assertEqual(decisions[-1]["attempt_id"], "attempt-1")

        state.writer_held = True
        before_writer_denial = len(decisions)
        writer_denied = self.host.review_candidate_content(task_id)
        self.assertEqual(writer_denied["error_code"], "candidate_writer_not_stopped")
        self.assertEqual(len(decisions), before_writer_denial)
        state.writer_held = False

        decision_mode[0] = "deny"
        denied = self.host.review_candidate_content(task_id)
        self.assertEqual(denied["error_code"], "content_disclosure_denied")
        decision_mode[0] = "wrong_digest"
        wrong_digest = self.host.review_candidate_content(task_id)
        self.assertEqual(wrong_digest["error_code"], "content_disclosure_binding_mismatch")
        decision_mode[0] = "wrong_path"
        wrong_path = self.host.review_candidate_content(task_id)
        self.assertEqual(wrong_path["error_code"], "content_disclosure_paths_invalid")
        decision_mode[0] = "approve"

        mutate_after_confirmation[0] = True
        changed = self.host.review_candidate_content(task_id)
        self.assertEqual(changed["status"], "failed")
        self.assertEqual(changed["error_code"], "candidate_changed_after_disclosure")
        self.assertNotIn("review", changed)
    def test_review_read_context_binds_session_attempt_and_revokes_during_confirmation(self):
        from types import SimpleNamespace
        (self.stable / "agent-core").mkdir()
        (self.stable / "agent-core" / "test.py").write_text("committed source\n", encoding="utf-8")
        self.git("-C", str(self.stable), "add", "agent-core/test.py")
        self.git("-C", str(self.stable), "commit", "--quiet", "-m", "allowed source")
        provider = HostRunContextProvider()
        live = [True]
        revoke = [False]
        prompts = []

        def approve(request):
            prompts.append(request)
            if revoke[0]:
                live[0] = False
            return ReviewDisclosureDecision(True, request["request_sha256"], ("agent-core/test.py",))

        self.host = _SnapshotTestHost(
            self.config, integrity_key=self.key, context_provider=provider,
            trusted_verifier_resolver=lambda *_args: None, trusted_review_disclosure_fn=approve)
        import self_evolution_workspace
        self.host._snapshot_materializer = _FixtureTrustedSnapshotMaterializer(shutil.which("git"))
        self.enterContext(mock.patch.object(self_evolution_workspace, "SafeSnapshotMaterializer", _FixtureTrustedSnapshotMaterializer))
        with provider.bind(self.context(), lambda: live[0]):
            created = self.host.create()
        self.assertEqual(created["status"], "created", created)
        task = created["task_id"]
        (self.trust / task / "agent-core/test.py").write_text("candidate change\n", encoding="utf-8")
        identity = EvolutionIdentity("snapshot-host-test", task, "attempt-1", "run-1", 1)
        self.host._tasks[task].identity = identity
        snapshot = SimpleNamespace(identity=identity, phase="awaiting_review", snapshot_revision=7)
        self.host._store = SimpleNamespace(read_task=lambda session, ident: snapshot if
                                          (session, ident) == (identity.session_id, task) else None)
        state = self.host._tasks[task]
        state.cancel_event = threading.Event()
        digest_one = self.host._current_candidate_digest(state)
        self.assertIsNotNone(digest_one)
        (self.trust / task / ".gitignore").write_text("ignored.tmp\n", encoding="utf-8")
        (self.trust / task / "ignored.tmp").write_text("ignored contents\n", encoding="utf-8")
        digest_two = self.host._current_candidate_digest(state)
        self.assertIsNotNone(digest_two)
        self.assertNotEqual(digest_one, digest_two)
        (self.trust / task / "ignored.tmp").write_text("updated contents\n", encoding="utf-8")
        self.assertNotEqual(digest_two, self.host._current_candidate_digest(state))
        original_capture = __import__("self_evolution_host").snapshot_review._capture
        capture_count = [0]
        def mutate_between_captures(root):
            tree = original_capture(root)
            capture_count[0] += 1
            if capture_count[0] == 1:
                (self.trust / task / "ignored.tmp").write_text("raced\n", encoding="utf-8")
            return tree
        with mock.patch("self_evolution_host.snapshot_review._capture", side_effect=mutate_between_captures):
            self.assertIsNone(self.host._current_candidate_digest(state))
        with mock.patch.object(self.host, "_persisted_candidate_binding_matches", return_value=True):
            with provider.bind_read("other-session", lambda: live[0]):
                self.assertNotEqual(self.host.review_candidate_content_for_session(task, "attempt-1")["status"], "reviewed")
            with provider.bind_read(identity.session_id, lambda: live[0]):
                self.assertNotEqual(self.host.review_candidate_content_for_session(task, "wrong-attempt")["status"], "reviewed")
                result = self.host.review_candidate_content_for_session(task, "attempt-1")
                self.assertEqual(result["status"], "reviewed", result)
                row = next(item for item in result["review"]["files"] if item["path"] == "agent-core/test.py")
                self.assertIn("+candidate change", row["diff"])
                self.assertEqual(prompts[-1]["attempt_id"], "attempt-1")
                # Exercise the real read dispatch and the bridge's attempt-bound
                # outbound approval path; experimental apply remains unavailable.
                import asyncio
                import contextvars
                import uuid
                from server import WsSessionHandler
                handler = WsSessionHandler.__new__(WsSessionHandler)
                handler.self_evolution_context_provider = provider
                session = SimpleNamespace(conversation_id=identity.session_id)
                repl = SimpleNamespace(session=session)
                sent = []
                from server import WsBridge
                from tests.isolation_helpers import make_temp_agent_paths
                bridge_paths = make_temp_agent_paths(self, prefix="snapshot-review-bridge-")
                bridge = None

                def emit(event):
                    sent.append(event)
                    if event.get("type") == "confirm.request":
                        # Exercise the connection's one-use, attempt-bound response path.
                        assert bridge.deliver_self_evolution_outbound_confirm(
                            event["request_id"], "y", task_id=event["task_id"],
                            attempt_id=event["attempt_id"], session_id=event["session_id"],
                            run_id=event["attempt_run_id"], turn_epoch=event["attempt_turn_epoch"],
                        )

                bridge = WsBridge(emit=emit, paths=bridge_paths, confirm_timeout=1)
                bridge._session_provider = lambda: repl.session
                bridge.self_evolution_chat = SimpleNamespace(
                    _trusted_worker_host=lambda: self.host,
                    _trusted_experimental_host=lambda: None,
                    native_apply_available=False,
                )
                request_id = str(uuid.uuid4())
                contextvars.Context().run(asyncio.run, handler._handle_incoming(
                    {"type": "self_evolution.review.get", "task_id": task,
                     "attempt_id": "attempt-1", "request_id": request_id}, repl, bridge))
                self.assertEqual(sent[-1]["type"], "self_evolution.review.result", sent)
                self.assertEqual(sent[-1]["request_id"], request_id)
                self.assertTrue(any(event.get("type") == "confirm.request"
                                    and event.get("confirm_kind") == "self_evolution_outbound"
                                    for event in sent))
                self.assertTrue(any(event.get("type") == "confirm.done"
                                    and event.get("confirm_kind") == "self_evolution_outbound"
                                    and event.get("choice") == "y" for event in sent))
                self.assertTrue(bridge.self_evolution_attempt_is_registered(identity))
                self.assertIn("+candidate change", next(row["diff"] for row in sent[-1]["files"]
                                                         if row["path"] == "agent-core/test.py"))
                self.assertIsNotNone(state.reviewed_content_binding)
                decision_mode = lambda request: ReviewDisclosureDecision(False, request["request_sha256"], ())
                denied_again = self.host._review_candidate_content_for_session_with_disclosure(
                    task, "attempt-1", decision_mode)
                self.assertNotEqual(denied_again["status"], "reviewed")
                self.assertIsNone(state.reviewed_content_binding)
                self.assertEqual(self.host.review_candidate_content_for_session(task, "attempt-1")["status"], "reviewed")
                blocked = self.host.apply_reviewed_candidate_for_session(task, "attempt-1")
                self.assertNotEqual(blocked["status"], "applied")
                self.assertEqual(blocked["error_code"], "native_apply_bridge_unavailable")
                with self.assertRaises(TypeError):
                    self.host.apply_reviewed_candidate_for_session(task, "attempt-1", selected_paths=("agent-core/test.py",))
                from self_evolution_merge_integration import NativeClaim
                claims = []
                self.host._trusted_selected_review_paths_fn = lambda request: ("agent-core/test.py",)
                def claim(request):
                    claims.append(request)
                    return NativeClaim(True, request["request_sha256"], request["operation_id"], request["selected_paths"])
                self.host._trusted_native_apply_claim_fn = claim
                candidate_file = self.trust / task / "agent-core/test.py"
                candidate_file.write_text("changed after review\n", encoding="utf-8")
                stale = self.host.apply_reviewed_candidate_for_session(task, "attempt-1")
                self.assertNotEqual(stale["status"], "applied")
                self.assertEqual(claims, [])
                self.assertEqual((self.stable / "agent-core/test.py").read_text(encoding="utf-8"), "committed source\n")
                candidate_file.write_text("candidate change\n", encoding="utf-8")
                self.assertEqual(self.host.review_candidate_content_for_session(task, "attempt-1")["status"], "reviewed")
                applied = self.host.apply_reviewed_candidate_for_session(task, "attempt-1")
                self.assertEqual(applied["status"], "applied", applied)
                self.assertEqual((self.stable / "agent-core/test.py").read_text(encoding="utf-8"), "candidate change\n")
                self.assertEqual(len(claims), 1)
                self.assertFalse(applied["git_merge_executed"])
                again = self.host.apply_reviewed_candidate_for_session(task, "attempt-1")
                self.assertEqual(again["error_code"], "reviewed_content_binding_unavailable")
                revoke[0] = True
                rejected = self.host.review_candidate_content_for_session(task, "attempt-1")
                self.assertNotEqual(rejected["status"], "reviewed")
                self.assertNotIn("review", rejected)

    def test_start_keeps_production_manifest_gate_closed_for_snapshot(self):
        # Do not fabricate a production-ready store/capability: the critical
        # assertion is that Host never treats this snapshot as full coverage.
        production_host = SelfEvolutionHost(
            self.config, integrity_key=self.key, context_provider=self.context,
            trusted_verifier_resolver=lambda *_args: None,
        )
        self.assertFalse(production_host.production_ready)
        self.assertFalse(production_host.full_candidate_coverage_available)
        self.assertFalse(production_host.snapshot_candidate_creation_available)


if __name__ == "__main__":
    unittest.main()
