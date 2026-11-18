"""Create and inspect isolated, untrusted self-evolution candidates.

The trust root and stable repository are supplied by the host. Linked candidates
share Git metadata and objects with the source. This is not an OS sandbox.
"""
from __future__ import annotations

import hashlib
import hmac
import locale
import json
import os
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from self_evolution_materializer import MaterializationError, SafeSnapshotMaterializer
from self_evolution_types import CandidateBinding
from self_evolution_windows_acl import WindowsAclError, audit_private_path


_MANIFEST_NAME = "self-evolution-workspace.json"
_LOCK_NAME = ".self-evolution-create.lock"
_MAX_MANIFEST_BYTES = 64 * 1024
_ROOT_ANCHOR_SCHEMA = "self-evolution-trust-root-anchor.v1"
_ROOT_ENROLLMENT_SCHEMA = "self-evolution-trust-root-enrollment.v1"
_ROOT_ANCHOR_MAX_BYTES = 16 * 1024
_MAX_GIT_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_REPORTED_CHANGES = 2000
_TASK_ID_FORMAT = uuid.UUID
# Do not enable linked-worktree checkout based on point-in-time configuration
# checks. The Git common directory is shared and can change between preflight
# and checkout; a safe plumbing materializer is required first.
_SAFE_CANDIDATE_CREATION_AVAILABLE = False


class WorkspaceError(Exception):
    """Sanitized operation failure; never contains raw subprocess output."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        stage: str,
        exit_code: int | None = None,
        publication_status: str | None = None,
        candidate_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.exit_code = exit_code
        self.publication_status = publication_status
        self.candidate_id = candidate_id

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "stage": self.stage, "message": str(self)}
        if self.exit_code is not None:
            result["exit_code"] = self.exit_code
        if self.publication_status is not None:
            result["publication_status"] = self.publication_status
        if self.candidate_id is not None:
            result["candidate_id"] = self.candidate_id
            result["task_id"] = self.candidate_id
        return result


class SelfEvolutionWorkspace:
    """Host-bound API for bounded candidate creation and read-only inspection.

    Parameters are trusted host configuration, not tool/model arguments. Public
    methods accept only generated UUID task identifiers; they never accept a
    repository path, destination path, or Git command from the caller.
    """

    def __init__(
        self,
        stable_repository: os.PathLike[str] | str,
        trust_root: os.PathLike[str] | str,
        integrity_key: bytes,
        *,
        enrollment_path: os.PathLike[str] | str | None = None,
        allow_initial_enrollment: bool = False,
    ) -> None:
        if not isinstance(integrity_key, bytes) or len(integrity_key) < 32:
            raise ValueError("Host integrity key must contain at least 32 bytes.")
        self._stable_repository = Path(os.path.abspath(os.fspath(stable_repository)))
        self._trust_root = Path(os.path.abspath(os.fspath(trust_root)))
        if type(allow_initial_enrollment) is not bool:
            raise ValueError("Initial trust-root enrollment policy must be explicit.")
        self._integrity_key = integrity_key
        self._allow_initial_enrollment = allow_initial_enrollment
        # Capture the host-configured trust root once. Per-call path identity is
        # not enough: it would silently bless a replacement directory.
        try:
            self._trust_root_identity = SafeSnapshotMaterializer._directory_identity(self._trust_root)
        except (MaterializationError, OSError, ValueError):
            # Defer malformed/missing-root diagnostics to the public operation;
            # creation still fails closed before acquiring or writing anything.
            self._trust_root_identity = None
        try:
            self._trust_root_acl_identity = audit_private_path(self._trust_root, expected_type="directory")
        except WindowsAclError:
            self._trust_root_acl_identity = None
        try:
            self._trust_root_parent_identity = SafeSnapshotMaterializer._directory_identity(self._trust_root.parent)
        except (MaterializationError, OSError, ValueError):
            self._trust_root_parent_identity = None
        try:
            self._trust_root_parent_acl_identity = audit_private_path(
                self._trust_root.parent, expected_type="directory",
            )
        except WindowsAclError:
            self._trust_root_parent_acl_identity = None
        self._trust_root_path_digest = self._path_digest(self._trust_root)
        self._trust_root_anchor_path = self._trust_root.parent / (
            f".self-evolution-root-anchor-{self._trust_root_path_digest}.json"
        )
        self._trust_root_anchor_lock_path = self._trust_root_anchor_path.with_name(
            self._trust_root_anchor_path.name + ".lock"
        )
        default_enrollment_path = self._trust_root.parent / f".self-evolution-root-enrollment-{self._trust_root_path_digest}.json"
        selected_enrollment_path = enrollment_path if enrollment_path is not None else default_enrollment_path
        self._trust_root_enrollment_path = Path(os.path.abspath(os.fspath(selected_enrollment_path)))
        try:
            self._enrollment_parent_identity = SafeSnapshotMaterializer._directory_identity(self._trust_root_enrollment_path.parent)
            self._enrollment_parent_acl_identity = audit_private_path(
                self._trust_root_enrollment_path.parent, expected_type="directory",
            )
        except (MaterializationError, OSError, ValueError, WindowsAclError):
            self._enrollment_parent_identity = None
            self._enrollment_parent_acl_identity = None

    def create(self) -> dict[str, Any]:
        """Create a task-specific linked worktree at the stable committed HEAD.

        Git's common directory is shared intentionally. Existing worktree files and
        indexes are not used as a destination and are never cleaned up here.
        """
        task_id = str(uuid.uuid4())
        candidate = self._trust_root / task_id
        stage = "preflight"
        lock_fd: int | None = None
        try:
            if not _SAFE_CANDIDATE_CREATION_AVAILABLE:
                raise WorkspaceError(
                    "Candidate creation is disabled until Git checkout is isolated from repository configuration and attributes races.",
                    code="safe_checkout_unavailable", stage="git_isolation",
                )
            self._validate_host_roots()
            stage = "reserve"
            lock_fd = self._acquire_lock()
            if candidate.exists() or candidate.is_symlink():
                raise WorkspaceError("Candidate target already exists; it was not changed.", code="target_exists", stage=stage)
            self._assert_no_reparse_components(candidate.parent)

            stage = "stable_identity"
            stable_identity = self._repository_identity(self._stable_repository, stage)
            stage = "git_safety_preflight"
            self._assert_safe_git_configuration(self._stable_repository, stable_identity, stage)
            stage = "stable_snapshot"
            stable_snapshot = self._worktree_snapshot(self._stable_repository, stage)
            stable_head = stable_snapshot["head"]
            stage = "pre_checkout_safety"
            self._assert_safe_git_configuration(self._stable_repository, stable_identity, stage)
            stage = "worktree_add"
            self._run_git("-C", str(self._stable_repository), "worktree", "add", "--detach", "--", str(candidate), stable_head, stage=stage)

            stage = "verify_candidate"
            self._validate_candidate_path(candidate)
            candidate_identity = self._repository_identity(candidate, stage)
            if candidate_identity["root"] != str(candidate.resolve()):
                raise WorkspaceError("Candidate checkout identity does not match its reserved path.", code="identity_mismatch", stage=stage)
            if candidate_identity["git_common_dir"] != stable_identity["git_common_dir"]:
                raise WorkspaceError("Candidate is not attached to the trusted Git common directory.", code="identity_mismatch", stage=stage)
            candidate_head = self._git_output(candidate, "rev-parse", "--verify", "HEAD^{commit}", stage=stage)
            if candidate_head != stable_head:
                raise WorkspaceError("Candidate HEAD differs from the captured stable HEAD.", code="head_mismatch", stage=stage)

            stage = "final_stable_check"
            final_stable_identity = self._repository_identity(self._stable_repository, stage)
            final_snapshot = self._worktree_snapshot(self._stable_repository, stage)
            if final_stable_identity != stable_identity or final_snapshot != stable_snapshot:
                raise WorkspaceError(
                    "Stable identity, HEAD, index, or Git status changed during creation; candidate was preserved for inspection.",
                    code="concurrent_state", stage=stage,
                )

            record = {
                "schema": "self-evolution-workspace.v2",
                "task_id": task_id,
                "status": "created",
                "stable_repository": stable_identity,
                "stable_head": stable_head,
                "candidate_repository": candidate_identity,
                "candidate_head": candidate_head,
                "candidate_path": str(candidate),
                "object_store": "shared_common_dir",
                "history_depth": None,
                "stable_worktree_content_verified": False,
                "os_sandbox": False,
            }
            baseline_record = {key: record[key] for key in (
                "task_id", "stable_repository", "stable_head", "candidate_repository", "candidate_head"
            )}
            record["baseline_manifest_digest"] = hashlib.sha256(
                json.dumps(baseline_record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            self._write_manifest(candidate, record)
            return record
        except WorkspaceError as exc:
            return self._failure(task_id, candidate, exc)
        except OSError:
            return self._failure(
                task_id, candidate,
                WorkspaceError("Filesystem operation failed; any created candidate state was preserved.",
                               code="filesystem_error", stage=stage),
            )
        finally:
            if lock_fd is not None:
                self._release_lock(lock_fd)

    def create_snapshot_candidate(self, materializer: SafeSnapshotMaterializer) -> dict[str, Any]:
        """Create a host-bound plain snapshot from the committed HEAD tree.

        This path deliberately avoids ``status``, checkout, and linked-worktree
        operations against shared Git metadata. The materializer copies and reads
        only the trusted object database using its private Git configuration,
        then exports regular blob contents and persists an authenticated binding.
        Production is enabled only when the configured trust-root identity is
        still the one captured by this host instance and platform operations are
        handle-relative. This remains candidate creation, not an OS sandbox.
        """
        if type(materializer) is not SafeSnapshotMaterializer:
            raise WorkspaceError("An exact host-configured safe snapshot materializer is required.",
                                 code="invalid_materializer", stage="snapshot_materialization")
        # Never execute methods on the supplied instance: even an exact-type
        # instance can shadow the cleanup gate or materialization methods.
        # Reconstruct from host configuration so the gate and methods come from
        # the trusted class; no caller-controlled instance overrides are copied.
        materializer = SafeSnapshotMaterializer(
            materializer.git_executable,
            max_object_store_bytes=materializer.max_object_store_bytes,
            max_object_bytes=materializer.max_object_bytes,
            max_total_bytes=materializer.max_total_bytes,
            max_tree_bytes=materializer.max_tree_bytes,
            max_files=materializer.max_files,
            max_object_files=materializer.max_object_files,
            timeout_seconds=materializer.timeout_seconds,
            authorized_parent_identity=self._trust_root_identity,
        )
        task_id = str(uuid.uuid4())
        candidate = self._trust_root / task_id
        self._validate_snapshot_host_roots()
        stage = "reserve"
        if candidate.exists() or candidate.is_symlink():
            raise WorkspaceError("Candidate target already exists; it was not changed.", code="target_exists", stage=stage)
        identity = self._repository_identity(self._stable_repository, "snapshot_stable_identity")
        stable_head = self._git_output(
            self._stable_repository, "rev-parse", "--verify", "HEAD^{commit}",
            stage="snapshot_stable_head",
        )
        object_directory = Path(identity["git_common_dir"]) / "objects"
        stage = "snapshot_materialization"
        published = False
        try:
            result = materializer.materialize_bound(
                object_directory, stable_head, candidate,
                candidate_id=task_id, integrity_key=self._integrity_key,
            )
            published = True
            binding = materializer.read_binding(
                candidate, expected_candidate_id=task_id, integrity_key=self._integrity_key,
                expected_parent_identity=self._trust_root_identity,
            )
        except Exception as exc:
            if isinstance(exc, WorkspaceError):
                raise
            code = getattr(exc, "code", "snapshot_creation_failed")
            raise WorkspaceError(
                "Isolated snapshot creation or identity validation failed; existing data was preserved.",
                code=str(code), stage=stage,
                publication_status=(getattr(exc, "publication_status", None)
                                    or ("published" if published else None)),
                candidate_id=(getattr(exc, "candidate_id", None)
                              or (task_id if published else None)),
            ) from exc

        expected_destination = os.path.normcase(os.path.abspath(os.fspath(candidate)))
        actual_destination = os.path.normcase(os.path.abspath(os.fspath(result.destination)))
        expected_path_digest = hashlib.sha256(os.fsencode(expected_destination)).hexdigest()
        binding_matches = (
            actual_destination == expected_destination
            and result.commit_oid.casefold() == stable_head.casefold()
            and binding.get("candidate_id") == task_id
            and binding.get("candidate_path_digest") == expected_path_digest
            and binding.get("commit_oid") == stable_head.casefold()
            and binding.get("tree_oid") == result.tree_oid.casefold()
            and binding.get("manifest_digest") == result.manifest_digest
            and binding.get("candidate_kind") == "materialized_snapshot"
            and binding.get("snapshot_scope") == "committed_tree_only"
            and binding.get("os_sandbox") is False
        )
        if not binding_matches:
            raise WorkspaceError(
                "Persisted candidate binding does not match the captured commit and publication.",
                code="snapshot_binding_mismatch", stage="snapshot_binding", candidate_id=task_id,
                publication_status="published",
            )

        try:
            final_identity = self._repository_identity(self._stable_repository, "snapshot_final_identity")
            final_head = self._git_output(
                self._stable_repository, "rev-parse", "--verify", "HEAD^{commit}",
                stage="snapshot_final_head",
            )
        except WorkspaceError as exc:
            raise WorkspaceError(
                "Snapshot was published and preserved, but the final stable-repository check failed.",
                code=exc.code, stage="snapshot_final_check", exit_code=exc.exit_code,
                publication_status="published", candidate_id=task_id,
            ) from exc
        if final_identity != identity or final_head != stable_head:
            raise WorkspaceError(
                "Stable repository identity or committed HEAD changed during snapshot creation; candidate was preserved for inspection.",
                code="concurrent_state", stage="snapshot_final_check", candidate_id=task_id,
                publication_status="published",
            )
        return {
            "status": "created",
            "task_id": task_id,
            "candidate_kind": "materialized_snapshot",
            "snapshot_scope": "committed_head_tree_only",
            "candidate_path": str(candidate),
            "stable_head": stable_head,
            "candidate_head": None,
            "baseline_manifest_digest": result.manifest_digest,
            "binding": binding,
            "os_sandbox": False,
            "merge_authorized": False,
        }

    def create_binding(self) -> CandidateBinding:
        """Create a candidate and return the shared typed host-binding contract.

        ``create`` remains the legacy dictionary API for existing callers.
        """
        record = self.create()
        if record.get("status") != "created":
            failure = record.get("failure", {})
            raise WorkspaceError(
                str(failure.get("message", "Candidate creation failed.")),
                code=str(failure.get("code", "creation_failed")),
                stage=str(failure.get("stage", "create")),
                exit_code=failure.get("exit_code"),
            )
        stable = record["stable_repository"]
        candidate = record["candidate_repository"]
        return CandidateBinding(
            candidate_id=record["task_id"],
            stable_commit=record["stable_head"],
            git_common_id=stable["git_common_dir"],
            stable_worktree_id=stable["git_dir"],
            candidate_worktree_id=candidate["git_dir"],
            baseline_manifest_digest=record["baseline_manifest_digest"],
        )

    def inspect(self, task_id: str) -> dict[str, Any]:
        """Return a validated persisted record without changing either repository."""
        if not _SAFE_CANDIDATE_CREATION_AVAILABLE:
            raise WorkspaceError(
                "Candidate inspection is disabled with candidate creation until the Git isolation boundary is safe.",
                code="safe_checkout_unavailable", stage="git_isolation",
            )
        task_id = self._validate_task_id(task_id)
        candidate = self._trust_root / task_id
        self._validate_candidate_path(candidate)
        record = self._read_manifest(candidate)
        if record.get("task_id") != task_id or record.get("candidate_path") != str(candidate):
            raise WorkspaceError("Candidate manifest identity does not match its location.", code="identity_mismatch", stage="inspect")
        self._validate_host_roots()
        stable_now = self._repository_identity(self._stable_repository, "inspect_stable_identity")
        candidate_now = self._repository_identity(candidate, "inspect_candidate_identity")
        if stable_now != record.get("stable_repository") or candidate_now != record.get("candidate_repository"):
            raise WorkspaceError("Stable or candidate Git worktree identity changed.", code="identity_mismatch", stage="inspect")
        return record

    def verify(self, task_id: str) -> dict[str, Any]:
        """Check identity and captured HEAD baselines; never grant merge authority.

        Working-tree differences (tracked and untracked) are returned as evidence,
        but do not invalidate identity/baseline matching or block later attribution.
        """
        try:
            record = self.inspect(task_id)
        except WorkspaceError as exc:
            if exc.code != "identity_mismatch":
                raise
            return {
                "task_id": task_id,
                "scope": "identity_and_head_baseline_only",
                "status": "identity_mismatch",
                "identity_matches": False,
                "baseline_matches": False,
                "merge_authorized": False,
                "failure": exc.as_dict(),
                "stable_worktree_changes": [],
                "candidate_worktree_changes": [],
            }
        result: dict[str, Any] = {
            "task_id": task_id,
            "scope": "identity_and_head_baseline_only",
            "status": "baseline_unchanged",
            "identity_matches": False,
            "baseline_matches": False,
            "merge_authorized": False,
            "recorded_stable_head": record.get("stable_head"),
            "recorded_candidate_head": record.get("candidate_head"),
            "stable_worktree_changes": [],
            "candidate_worktree_changes": [],
        }
        try:
            self._validate_host_roots()
            candidate = self._trust_root / task_id
            stable_now = self._repository_identity(self._stable_repository, "verify_stable_identity")
            candidate_now = self._repository_identity(candidate, "verify_candidate_identity")
            result["current_stable_repository"] = stable_now
            result["current_candidate_repository"] = candidate_now
            if stable_now != record.get("stable_repository") or candidate_now != record.get("candidate_repository"):
                result.update(status="identity_mismatch")
                return result
            result["identity_matches"] = True
            stable_head = self._git_output(self._stable_repository, "rev-parse", "--verify", "HEAD^{commit}", stage="verify_stable_head")
            candidate_head = self._git_output(candidate, "rev-parse", "--verify", "HEAD^{commit}", stage="verify_candidate_head")
            result["current_stable_head"] = stable_head
            result["current_candidate_head"] = candidate_head
            stable_head_matches = stable_head == record.get("stable_head")
            candidate_head_matches = candidate_head == record.get("candidate_head")
            result["stable_head_matches"] = stable_head_matches
            result["candidate_head_matches"] = candidate_head_matches
            stable_changes = self._worktree_changes(self._stable_repository, "verify_stable_status")
            candidate_changes = self._worktree_changes(candidate, "verify_candidate_status")
            result["stable_worktree_changes"] = stable_changes
            result["candidate_worktree_changes"] = candidate_changes
            result["baseline_matches"] = stable_head_matches and candidate_head_matches
            if not stable_head_matches:
                result["status"] = "stable_head_drift"
            elif not candidate_head_matches:
                result["status"] = "candidate_head_drift"
        except WorkspaceError as exc:
            result.update(status="verification_failed", failure=exc.as_dict())
        return result

    def _validate_host_roots(self) -> None:
        self._assert_no_reparse_components(self._stable_repository)
        self._assert_no_reparse_components(self._trust_root)
        if not self._stable_repository.is_dir():
            raise WorkspaceError("Configured stable repository is not a directory.", code="not_git_repository", stage="preflight")
        if not self._trust_root.is_dir():
            raise WorkspaceError("Configured trust root must already exist as a directory.", code="invalid_trust_root", stage="preflight")
        current_root_identity = SafeSnapshotMaterializer._directory_identity(self._trust_root)
        if self._trust_root_identity is None or current_root_identity != self._trust_root_identity:
            raise WorkspaceError(
                "Configured candidate root no longer matches the host-bound directory identity.",
                code="trust_root_identity_mismatch", stage="preflight",
            )
        self._repository_identity(self._stable_repository, "stable_identity")

    def _validate_snapshot_host_roots(self) -> None:
        self._validate_host_roots()
        if os.name != "nt":
            raise WorkspaceError(
                "Host-owned candidate creation requires a platform ACL and handle-relative filesystem proof.",
                code="safe_checkout_unavailable", stage="preflight",
            )
        try:
            current_acl_identity = audit_private_path(self._trust_root, expected_type="directory")
        except WindowsAclError as exc:
            raise WorkspaceError(
                "Candidate root ownership and permissions are not proven private.",
                code="untrusted_candidate_root", stage="preflight",
            ) from exc
        if self._trust_root_acl_identity is None or current_acl_identity != self._trust_root_acl_identity:
            raise WorkspaceError(
                "Candidate root no longer matches the host-audited directory identity.",
                code="trust_root_identity_mismatch", stage="preflight",
            )
        try:
            parent_identity = SafeSnapshotMaterializer._directory_identity(self._trust_root.parent)
            parent_acl_identity = audit_private_path(self._trust_root.parent, expected_type="directory")
        except (MaterializationError, OSError, ValueError, WindowsAclError) as exc:
            raise WorkspaceError(
                "Persistent trust-root anchor parent is unavailable or not private.",
                code="untrusted_candidate_root", stage="trust_root_anchor",
            ) from exc
        if (self._trust_root_parent_identity is None
                or parent_identity != self._trust_root_parent_identity
                or self._trust_root_parent_acl_identity is None
                or parent_acl_identity != self._trust_root_parent_acl_identity):
            raise WorkspaceError(
                "Persistent trust-root anchor parent no longer matches its host-bound identity.",
                code="trust_root_identity_mismatch", stage="trust_root_anchor",
            )
        self._ensure_independent_trust_root_enrollment()
        self._ensure_persistent_trust_root_anchor()

    @staticmethod
    def _path_digest(path: Path) -> str:
        normalized = os.path.normcase(os.path.abspath(os.fspath(path)))
        return hashlib.sha256(os.fsencode(normalized)).hexdigest()

    def _root_anchor_record(self) -> dict[str, Any]:
        root_identity = SafeSnapshotMaterializer._directory_identity(self._trust_root)
        parent_identity = SafeSnapshotMaterializer._directory_identity(self._trust_root.parent)
        root_acl_identity = audit_private_path(self._trust_root, expected_type="directory")
        parent_acl_identity = audit_private_path(self._trust_root.parent, expected_type="directory")
        if (root_identity != self._trust_root_identity
                or parent_identity != self._trust_root_parent_identity
                or root_acl_identity != self._trust_root_acl_identity
                or parent_acl_identity != self._trust_root_parent_acl_identity):
            raise WorkspaceError(
                "Trust-root or anchor-parent identity changed while validating the persistent anchor.",
                code="trust_root_identity_mismatch", stage="trust_root_anchor",
            )
        return {
            "schema": _ROOT_ANCHOR_SCHEMA,
            "trust_root_path_digest": self._trust_root_path_digest,
            "trust_root_directory_identity": list(root_identity),
            "trust_root_parent_identity": list(parent_identity),
            "trust_root_acl_identity": list(root_acl_identity),
            "trust_root_parent_acl_identity": list(parent_acl_identity),
        }

    def _ensure_independent_trust_root_enrollment(self) -> None:
        """Require independent HMAC-protected evidence before root anchoring."""
        path = self._trust_root_enrollment_path
        try:
            path.relative_to(self._trust_root)
        except ValueError:
            pass
        else:
            raise WorkspaceError("Trust-root enrollment evidence must be stored outside the candidate root.",
                                 code="trust_root_enrollment_path_invalid", stage="trust_root_enrollment")
        if path == self._trust_root_anchor_path:
            raise WorkspaceError("Trust-root enrollment evidence must be independent of the root anchor.",
                                 code="trust_root_enrollment_path_invalid", stage="trust_root_enrollment")
        lock = path.with_name(path.name + ".lock")
        try:
            self._assert_no_reparse_components(path.parent)
            parent_identity = SafeSnapshotMaterializer._directory_identity(path.parent)
            parent_acl = audit_private_path(path.parent, expected_type="directory")
        except (MaterializationError, OSError, ValueError, WindowsAclError) as exc:
            raise WorkspaceError("Independent trust-root enrollment storage is unavailable or not private.",
                                 code="trust_root_enrollment_unavailable", stage="trust_root_enrollment") from exc
        if (self._enrollment_parent_identity is None or self._enrollment_parent_acl_identity is None
                or parent_identity != self._enrollment_parent_identity
                or parent_acl != self._enrollment_parent_acl_identity):
            raise WorkspaceError("Independent enrollment storage no longer matches its host-bound identity.",
                                 code="trust_root_enrollment_storage_mismatch", stage="trust_root_enrollment")
        if path.exists() or path.is_symlink():
            self._read_independent_trust_root_enrollment(path, parent_identity, parent_acl)
            return
        if not self._allow_initial_enrollment:
            raise WorkspaceError("No protected trust-root enrollment exists; explicit initial enrollment is required.",
                                 code="trust_root_enrollment_required", stage="trust_root_enrollment")
        if self._trust_root_anchor_path.exists() or self._trust_root_anchor_path.is_symlink():
            raise WorkspaceError("Root anchor exists without independent enrollment evidence; refusing re-enrollment.",
                                 code="trust_root_enrollment_missing", stage="trust_root_enrollment")
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            # Wait briefly for the lock owner, then accept only a completed,
            # authenticated enrollment. Never take over its initialization.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if path.exists():
                    self._read_independent_trust_root_enrollment(path, parent_identity, parent_acl)
                    return
                if not lock.exists():
                    break
                time.sleep(0.02)
            raise WorkspaceError("Trust-root enrollment is being initialized or unresolved; no state was changed.",
                                 code="trust_root_enrollment_busy", stage="trust_root_enrollment") from exc
        lock_stat = os.fstat(lock_fd)
        try:
            if path.exists() or path.is_symlink():
                self._read_independent_trust_root_enrollment(path, parent_identity, parent_acl)
                return
            if next(self._trust_root.iterdir(), None) is not None:
                raise WorkspaceError("Trust root is not empty during initial enrollment; state was preserved.",
                                     code="trust_root_enrollment_missing", stage="trust_root_enrollment")
            record = self._root_anchor_record()
            record["schema"] = _ROOT_ENROLLMENT_SCHEMA
            record["enrollment_storage_parent_identity"] = list(parent_identity)
            record["enrollment_storage_parent_acl_identity"] = list(parent_acl)
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            envelope = {"record": record, "hmac_sha256": hmac.new(self._integrity_key, canonical, hashlib.sha256).hexdigest()}
            payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._read_independent_trust_root_enrollment(path, parent_identity, parent_acl)
        except FileExistsError as exc:
            if path.exists():
                self._read_independent_trust_root_enrollment(path, parent_identity, parent_acl)
                return
            raise WorkspaceError("Trust-root enrollment appeared concurrently; no file was overwritten.",
                                 code="trust_root_enrollment_busy", stage="trust_root_enrollment") from exc
        except WorkspaceError:
            raise
        except (MaterializationError, OSError, ValueError, WindowsAclError) as exc:
            raise WorkspaceError("Protected trust-root enrollment could not be written; existing evidence was preserved.",
                                 code="trust_root_enrollment_io", stage="trust_root_enrollment") from exc
        finally:
            os.close(lock_fd)
            try:
                current = lock.lstat()
                if (int(current.st_dev), int(current.st_ino)) == (int(lock_stat.st_dev), int(lock_stat.st_ino)):
                    lock.unlink()
            except OSError:
                pass

    def _read_independent_trust_root_enrollment(self, path: Path, parent_identity, parent_acl) -> None:
        try:
            info = path.lstat()
            if SafeSnapshotMaterializer._is_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_size > _ROOT_ANCHOR_MAX_BYTES:
                raise ValueError("unsafe enrollment file")
            acl_before = audit_private_path(path, expected_type="file")
            raw = path.read_bytes()
            acl_after = audit_private_path(path, expected_type="file")
            if acl_before != acl_after or path.stat().st_size != len(raw):
                raise ValueError("enrollment identity changed while reading")
            envelope = json.loads(raw.decode("utf-8"))
            if type(envelope) is not dict or set(envelope) != {"record", "hmac_sha256"} or type(envelope["record"]) is not dict:
                raise ValueError("invalid enrollment envelope")
            record = envelope["record"]
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = hmac.new(self._integrity_key, canonical, hashlib.sha256).hexdigest()
            if not isinstance(envelope["hmac_sha256"], str) or not hmac.compare_digest(envelope["hmac_sha256"], expected):
                raise WorkspaceError("Protected trust-root enrollment authentication failed; evidence was preserved.",
                                     code="trust_root_enrollment_tampered", stage="trust_root_enrollment")
            expected_record = self._root_anchor_record()
            expected_record["schema"] = _ROOT_ENROLLMENT_SCHEMA
            expected_record["enrollment_storage_parent_identity"] = list(parent_identity)
            expected_record["enrollment_storage_parent_acl_identity"] = list(parent_acl)
            if record != expected_record:
                raise WorkspaceError("Protected enrollment does not match the configured trust root; evidence was preserved.",
                                     code="trust_root_identity_mismatch", stage="trust_root_enrollment")
        except WorkspaceError:
            raise
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, WindowsAclError) as exc:
            raise WorkspaceError("Protected trust-root enrollment is unreadable or corrupt; evidence was preserved.",
                                 code="trust_root_enrollment_corrupt", stage="trust_root_enrollment") from exc
    def _ensure_persistent_trust_root_anchor(self) -> None:
        """Authenticate the host root identity outside the replaceable root.

        A missing anchor is initialized only for an empty trust root. Existing
        malformed, unauthenticated, mismatched, or locked state is never reset.
        """
        anchor = self._trust_root_anchor_path
        lock_path = self._trust_root_anchor_lock_path
        self._assert_no_reparse_components(anchor.parent)
        self._assert_no_reparse_components(anchor)
        self._assert_no_reparse_components(lock_path)
        if lock_path.exists() or lock_path.is_symlink():
            raise WorkspaceError(
                "Persistent trust-root anchor initialization is locked or unresolved; state was preserved.",
                code="trust_root_anchor_busy", stage="trust_root_anchor",
            )
        if anchor.exists() or anchor.is_symlink():
            self._read_persistent_trust_root_anchor()
            return

        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise WorkspaceError(
                "Another host is initializing the persistent trust-root anchor; no state was changed.",
                code="trust_root_anchor_busy", stage="trust_root_anchor",
            ) from exc
        except OSError as exc:
            raise WorkspaceError(
                "Persistent trust-root anchor lock could not be acquired safely.",
                code="trust_root_anchor_io", stage="trust_root_anchor",
            ) from exc

        lock_stat = os.fstat(lock_fd)
        try:
            # Another initializer may have completed between our first check
            # and lock acquisition. Re-read under the exclusive lock.
            if anchor.exists() or anchor.is_symlink():
                self._read_persistent_trust_root_anchor()
                return
            try:
                if next(self._trust_root.iterdir(), None) is not None:
                    raise WorkspaceError(
                        "Trust-root anchor is missing while candidate-root state already exists; refusing reset.",
                        code="trust_root_anchor_missing", stage="trust_root_anchor",
                    )
                record = self._root_anchor_record()
            except WorkspaceError:
                raise
            except (OSError, WindowsAclError, MaterializationError, ValueError) as exc:
                raise WorkspaceError(
                    "Host trust-root identity could not be captured for persistent authentication.",
                    code="trust_root_anchor_io", stage="trust_root_anchor",
                ) from exc
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            envelope = {
                "record": record,
                "hmac_sha256": hmac.new(self._integrity_key, canonical, hashlib.sha256).hexdigest(),
            }
            payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if len(payload) > _ROOT_ANCHOR_MAX_BYTES:
                raise WorkspaceError(
                    "Persistent trust-root anchor exceeds the size limit.",
                    code="trust_root_anchor_corrupt", stage="trust_root_anchor",
                )
            try:
                anchor_fd = os.open(anchor, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                raise WorkspaceError(
                    "Persistent trust-root anchor appeared concurrently; it was not overwritten.",
                    code="trust_root_anchor_busy", stage="trust_root_anchor",
                ) from exc
            try:
                with os.fdopen(anchor_fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                # Leave a possibly partial anchor in place. Future attempts
                # must report corruption instead of silently replacing it.
                raise WorkspaceError(
                    "Persistent trust-root anchor write failed; partial state was preserved.",
                    code="trust_root_anchor_io", stage="trust_root_anchor",
                ) from exc
            self._read_persistent_trust_root_anchor()
        finally:
            os.close(lock_fd)
            try:
                current = lock_path.lstat()
                if ((int(current.st_dev), int(current.st_ino))
                        == (int(lock_stat.st_dev), int(lock_stat.st_ino))):
                    lock_path.unlink()
            except OSError:
                # A leftover lock is a recovery condition, not permission to
                # remove a possibly replaced lock or rewrite the anchor.
                pass

    def _read_persistent_trust_root_anchor(self) -> dict[str, Any]:
        anchor = self._trust_root_anchor_path
        self._assert_no_reparse_components(anchor)
        try:
            info = anchor.lstat()
            if (SafeSnapshotMaterializer._is_reparse(info) or not stat.S_ISREG(info.st_mode)
                    or info.st_size > _ROOT_ANCHOR_MAX_BYTES):
                raise WorkspaceError(
                    "Persistent trust-root anchor is unsafe or oversized; it was preserved.",
                    code="trust_root_anchor_corrupt", stage="trust_root_anchor",
                )
            acl_identity_before = audit_private_path(anchor, expected_type="file")
            raw = anchor.read_bytes()
            acl_identity_after = audit_private_path(anchor, expected_type="file")
            if acl_identity_before != acl_identity_after:
                raise WorkspaceError(
                    "Persistent trust-root anchor changed while being read; it was preserved.",
                    code="trust_root_anchor_corrupt", stage="trust_root_anchor",
                )
            if anchor.stat().st_size != len(raw):
                raise WorkspaceError(
                    "Persistent trust-root anchor changed while being read; it was preserved.",
                    code="trust_root_anchor_corrupt", stage="trust_root_anchor",
                )
            envelope = json.loads(raw.decode("utf-8"))
            if (not isinstance(envelope, dict) or set(envelope) != {"record", "hmac_sha256"}
                    or not isinstance(envelope.get("record"), dict)):
                raise ValueError("invalid anchor envelope")
            record = envelope["record"]
            required = {
                "schema", "trust_root_path_digest", "trust_root_directory_identity",
                "trust_root_parent_identity", "trust_root_acl_identity", "trust_root_parent_acl_identity",
            }
            if set(record) != required:
                raise ValueError("invalid anchor record")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = hmac.new(self._integrity_key, canonical, hashlib.sha256).hexdigest()
            signature = envelope.get("hmac_sha256")
            if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
                raise WorkspaceError(
                    "Persistent trust-root anchor authentication failed; it was preserved.",
                    code="trust_root_anchor_tampered", stage="trust_root_anchor",
                )
        except WorkspaceError:
            raise
        except (OSError, ValueError, UnicodeError, TypeError, KeyError, WindowsAclError) as exc:
            raise WorkspaceError(
                "Persistent trust-root anchor is unreadable or corrupt; it was preserved.",
                code="trust_root_anchor_corrupt", stage="trust_root_anchor",
            ) from exc

        try:
            expected_record = self._root_anchor_record()
        except WorkspaceError:
            raise
        except (OSError, ValueError, WindowsAclError, MaterializationError) as exc:
            raise WorkspaceError(
                "Current host trust-root identity could not be verified.",
                code="untrusted_candidate_root", stage="trust_root_anchor",
            ) from exc
        if record != expected_record:
            raise WorkspaceError(
                "Persistent anchor belongs to a different trust-root identity; both roots were preserved.",
                code="trust_root_identity_mismatch", stage="trust_root_anchor",
            )
        return record

    def _validate_candidate_path(self, candidate: Path) -> None:
        self._assert_no_reparse_components(self._trust_root)
        self._assert_no_reparse_components(candidate)
        root = self._trust_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError("Candidate path escapes the configured trust root.", code="path_escape", stage="inspect") from exc
        if not resolved.is_dir():
            raise WorkspaceError("Candidate path is not a directory.", code="candidate_missing", stage="inspect")

    @staticmethod
    def _assert_no_reparse_components(path: Path) -> None:
        absolute = Path(os.path.abspath(path))
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current = current / component
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            attributes = getattr(info, "st_file_attributes", 0)
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(info.st_mode) or attributes & reparse:
                raise WorkspaceError("Path contains a symbolic link or reparse point.", code="linked_path", stage="path_check")

    def _repository_identity(self, path: Path, stage: str) -> dict[str, str]:
        try:
            root = self._git_output(path, "rev-parse", "--show-toplevel", stage=stage)
            common = self._git_output(path, "rev-parse", "--git-common-dir", stage=stage)
        except WorkspaceError as exc:
            if exc.code == "git_failed" and stage == "stable_identity":
                raise WorkspaceError("Configured stable path is not a Git repository.", code="not_git_repository", stage=stage, exit_code=exc.exit_code) from exc
            raise
        root_path = Path(root).resolve(strict=True)
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = path / common_path
        common_path = common_path.resolve(strict=True)
        git_dir = self._git_output(path, "rev-parse", "--absolute-git-dir", stage=stage)
        git_dir_path = Path(git_dir).resolve(strict=True)
        self._assert_no_reparse_components(root_path)
        self._assert_no_reparse_components(common_path)
        self._assert_no_reparse_components(git_dir_path)
        return {"root": str(root_path), "git_common_dir": str(common_path), "git_dir": str(git_dir_path)}

    @staticmethod
    def _safe_git_environment() -> dict[str, str]:
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        })
        return env

    def _git_output(self, cwd: Path, *args: str, stage: str) -> str:
        result = self._run_git("-C", str(cwd), *args, stage=stage, want_output=True)
        return result.rstrip("\r\n")

    def _worktree_changes(self, path: Path, stage: str) -> dict[str, Any]:
        self._assert_safe_git_configuration(path, self._repository_identity(path, stage), stage)
        raw = self._git_output(path, "status", "--porcelain=v1", "-z", "--untracked-files=all", stage=stage)
        entries = raw.split("\0")
        changes: list[dict[str, str]] = []
        index = 0
        total = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            total += 1
            status = entry[:2]
            item = {"status": status, "path": entry[3:] if len(entry) > 3 else ""}
            if "R" in status or "C" in status:
                if index < len(entries) and entries[index]:
                    item["related_path"] = entries[index]
                    index += 1
            if len(changes) < _MAX_REPORTED_CHANGES:
                changes.append(item)
        return {"count": total, "entries": changes, "truncated": total > len(changes)}

    def _assert_safe_git_configuration(self, path: Path, identity: dict[str, str], stage: str) -> None:
        """Reject executable filters and attribute sources after status/checkout.

        These checks are not an interprocess lock: a trusted host must prevent
        concurrent mutation of common Git config/attributes while Git runs.
        """
        config = self._git_output(path, "config", "--null", "--list", stage=stage)
        for entry in config.split("\0"):
            key = entry.split("\n", 1)[0].casefold()
            if key.startswith("filter."):
                raise WorkspaceError("Repository filter configuration is not permitted.", code="unsafe_git_configuration", stage=stage)
        common = Path(identity["git_common_dir"])
        info_attributes = common / "info" / "attributes"
        self._assert_no_reparse_components(info_attributes)
        if info_attributes.exists():
            raise WorkspaceError("Repository info attributes are not permitted.", code="unsafe_git_configuration", stage=stage)
        for args in (("ls-tree", "-r", "--name-only", "-z", "HEAD"),
                     ("ls-files", "--cached", "--others", "-z")):
            names = self._git_output(path, *args, stage=stage)
            if any(name.replace("\\", "/").split("/")[-1].casefold() == ".gitattributes"
                   for name in names.split("\0") if name):
                raise WorkspaceError("Repository attributes are not permitted during candidate creation or inspection.",
                                     code="unsafe_git_configuration", stage=stage)

    def _worktree_snapshot(self, path: Path, stage: str) -> dict[str, str]:
        # Status identifies paths/codes, not the bytes of already-dirty files.
        self._assert_safe_git_configuration(path, self._repository_identity(path, stage), stage)
        head = self._git_output(path, "rev-parse", "--verify", "HEAD^{commit}", stage=stage)
        status = self._git_output(path, "status", "--porcelain=v1", "-z", "--untracked-files=all", stage=stage)
        index_name = self._git_output(path, "rev-parse", "--git-path", "index", stage=stage)
        index_path = Path(index_name)
        if not index_path.is_absolute():
            index_path = path / index_path
        self._assert_no_reparse_components(index_path)
        try:
            index_digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise WorkspaceError("Git index could not be safely inspected.", code="index_unavailable", stage=stage) from exc
        return {"head": head, "status_sha256": hashlib.sha256(status.encode("utf-8", errors="surrogatepass")).hexdigest(),
                "index_sha256": index_digest}

    def _run_git(self, *args: str, stage: str, want_output: bool = False) -> str:
        stdout_chunks: list[bytes] = []
        overflow = threading.Event()
        proc: subprocess.Popen[bytes] | None = None
        try:
            proc = subprocess.Popen(
                ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
                 "-c", f"core.attributesFile={os.devnull}", *args], stdout=subprocess.PIPE if want_output else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=False, env=self._safe_git_environment(),
            )
        except FileNotFoundError as exc:
            raise WorkspaceError("Git executable is unavailable.", code="git_unavailable", stage=stage) from exc
        except OSError as exc:
            raise WorkspaceError("Could not start Git; no raw process diagnostics were exposed.", code="git_unavailable", stage=stage) from exc

        def drain_bounded_stdout() -> None:
            assert proc is not None and proc.stdout is not None
            total = 0
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    return
                if total + len(chunk) > _MAX_GIT_OUTPUT_BYTES:
                    overflow.set()
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    return
                stdout_chunks.append(chunk)
                total += len(chunk)

        reader: threading.Thread | None = None
        if want_output:
            reader = threading.Thread(target=drain_bounded_stdout, name="bounded-git-output", daemon=True)
            reader.start()
        try:
            return_code = proc.wait(timeout=120)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            if reader is not None:
                reader.join(timeout=2)
            raise WorkspaceError("Git operation exceeded its time limit; candidate state was preserved.", code="git_timeout", stage=stage) from exc
        if reader is not None:
            reader.join(timeout=2)
            if reader.is_alive():
                proc.kill()
                reader.join(timeout=2)
                if proc.stdout is not None:
                    proc.stdout.close()
                raise WorkspaceError("Could not finish bounded Git output collection.", code="git_output_error", stage=stage)
            if proc.stdout is not None:
                proc.stdout.close()
        if overflow.is_set():
            raise WorkspaceError("Git output exceeded the bounded inspection limit and the process was terminated.", code="git_output_limit", stage=stage)
        if return_code:
            raise WorkspaceError(
                f"Git operation failed during {stage}; candidate state was preserved.",
                code="git_failed", stage=stage, exit_code=return_code,
            )
        if not want_output:
            return ""
        encoding = locale.getpreferredencoding(False) or "utf-8"
        return b"".join(stdout_chunks).decode(encoding, errors="replace")

    def _acquire_lock(self) -> int:
        lock_path = self._trust_root / _LOCK_NAME
        self._assert_no_reparse_components(lock_path.parent)
        try:
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise WorkspaceError(
                "Candidate creation is already active or has an unresolved prior lock; no state was changed.",
                code="concurrent_state", stage="reserve",
            ) from exc

    def _release_lock(self, fd: int) -> None:
        path = self._trust_root / _LOCK_NAME
        try:
            os.close(fd)
            path.unlink()
        except OSError:
            pass

    def _write_manifest(self, candidate: Path, record: dict[str, Any]) -> None:
        manifest = self._trust_root / f"{candidate.name}.{_MANIFEST_NAME}"
        self._assert_no_reparse_components(self._trust_root)
        if not candidate.is_dir() or candidate.is_symlink():
            raise WorkspaceError("Candidate worktree is missing or linked.", code="candidate_invalid", stage="manifest")
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        envelope = {
            "record": record,
            "hmac_sha256": hmac.new(self._integrity_key, canonical, hashlib.sha256).hexdigest(),
        }
        payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise WorkspaceError("Candidate manifest exceeds the size limit.", code="manifest_too_large", stage="manifest")
        fd = os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def _read_manifest(self, candidate: Path) -> dict[str, Any]:
        self._validate_candidate_path(candidate)
        manifest = self._trust_root / f"{candidate.name}.{_MANIFEST_NAME}"
        self._assert_no_reparse_components(manifest)
        try:
            if manifest.stat().st_size > _MAX_MANIFEST_BYTES:
                raise WorkspaceError("Candidate manifest exceeds the size limit.", code="manifest_too_large", stage="inspect")
            envelope = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or not isinstance(envelope.get("record"), dict):
                raise WorkspaceError("Candidate manifest schema is invalid.", code="manifest_invalid", stage="inspect")
            record = envelope["record"]
            signature = envelope.get("hmac_sha256")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = hmac.new(self._integrity_key, canonical, hashlib.sha256).hexdigest()
            if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
                raise WorkspaceError("Candidate manifest integrity check failed.", code="manifest_tampered", stage="inspect")
        except WorkspaceError:
            raise
        except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
            raise WorkspaceError("Candidate manifest is missing or invalid.", code="manifest_invalid", stage="inspect") from exc
        if record.get("schema") not in ("self-evolution-workspace.v1", "self-evolution-workspace.v2"):
            raise WorkspaceError("Candidate manifest schema is invalid.", code="manifest_invalid", stage="inspect")
        return record

    @staticmethod
    def _validate_task_id(task_id: str) -> str:
        try:
            parsed = _TASK_ID_FORMAT(task_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise WorkspaceError("Task identifier must be a UUID.", code="invalid_task_id", stage="validate_task_id") from exc
        canonical = str(parsed)
        if canonical != task_id:
            raise WorkspaceError("Task identifier must use canonical UUID form.", code="invalid_task_id", stage="validate_task_id")
        return canonical

    @staticmethod
    def _failure(task_id: str, candidate: Path, exc: WorkspaceError) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "creation_failed",
            "task_id": task_id,
            "candidate_path": str(candidate),
            "failure": exc.as_dict(),
            "scene_preserved": candidate.exists(),
            "os_sandbox": False,
        }
        return result
