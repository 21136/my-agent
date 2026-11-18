"""Trusted self-evolution host with optional, explicitly injected worker wiring.

Legacy create/inspect/attribute/verify operations remain available without a
worker. Persistent start/dispatch is enabled only when a trusted EvolutionStore
and TrustedWorkerCapabilities are injected; defaults remain unbound. This module
does not provide an OS sandbox, cross-turn/session event delivery, restart
reconciliation of an active HostRunContext, or native merge/adoption confirmation.
"""
from __future__ import annotations

import hashlib
import ast
from contextlib import contextmanager
from contextvars import ContextVar
import hmac
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

from self_evolution_attribution import Baseline, AttributionError, capture_baseline, check_changes
from self_evolution_candidate_tools import build_self_evolution_candidate_tools
from self_evolution_store import (EventPage, EvolutionStore, StartRequestClaim, StoreConflict,
                                  StoreError, StoreOwnershipError)
from self_evolution_types import (CandidateBinding, CancelReceipt, EvolutionEvent, EvolutionIdentity,
                                  EvolutionRequest, EvolutionSnapshot, VerificationEvidence)
from self_evolution_worker import (DEFAULT_MAX_REQUESTS, MAX_REQUESTS, ConfirmedToolClient, HostTool,
                                   SelfEvolutionWorkerContext, SelfEvolutionWorkerResult,
                                   run_self_evolution_worker)
from self_evolution_guidance import GuidanceInbox
from self_evolution_verification import VerificationResult, VerificationRunner, WorkspaceBinding
from self_evolution_workspace import SelfEvolutionWorkspace, WorkspaceError
from self_evolution_materializer import SafeSnapshotMaterializer
from self_evolution_manifest import capture_tree_manifest
import self_evolution_snapshot_review as snapshot_review


@dataclass(frozen=True)
class SelfEvolutionHostConfig:
    """Immutable trusted Host configuration; never construct this from tool input."""

    stable_repository: Path | str
    trust_root: Path | str
    attribution_paths: tuple[str, ...]
    verification_source_paths: tuple[str, ...]
    verification_commands: Mapping[str, Sequence[str]]
    verification_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        stable = Path(os.path.abspath(os.fspath(self.stable_repository)))
        trust = Path(os.path.abspath(os.fspath(self.trust_root)))
        attr = _checked_relative_paths(self.attribution_paths, "attribution_paths")
        sources = _checked_relative_paths(self.verification_source_paths, "verification_source_paths")
        if stable == trust or stable in trust.parents or trust in stable.parents:
            raise ValueError("stable repository and candidate trust root must be disjoint")
        if not isinstance(self.verification_commands, Mapping):
            raise ValueError("fixed verification command mapping is required")
        commands: dict[str, tuple[str, ...]] = {}
        for key, argv in self.verification_commands.items():
            if (type(key) is not str or not key or len(key) > 100
                    or isinstance(argv, (str, bytes)) or not isinstance(argv, (tuple, list))
                    or not argv or any(type(arg) is not str or not arg or "\x00" in arg for arg in argv)):
                raise ValueError("invalid fixed verification command mapping")
            commands[key] = tuple(argv)
        if (not isinstance(self.verification_timeout_seconds, (int, float))
                or isinstance(self.verification_timeout_seconds, bool)
                or not 0 < self.verification_timeout_seconds <= 86_400):
            raise ValueError("verification timeout is out of range")
        object.__setattr__(self, "stable_repository", stable)
        object.__setattr__(self, "trust_root", trust)
        object.__setattr__(self, "attribution_paths", attr)
        object.__setattr__(self, "verification_source_paths", sources)
        object.__setattr__(self, "verification_commands", MappingProxyType(commands))


@dataclass(frozen=True)
class HostRunContext:
    """Snapshot returned only by the backend-injected live run provider."""

    conversation_id: str
    run_id: str
    turn_epoch: int
    mode: str
    work_intent: str
    cancel_event: threading.Event
    request_id: str
    request_origin: str
    active: bool = True
    self_evolution_intent: bool = False

    def identity(self) -> tuple[str, str, int, str]:
        return self.conversation_id, self.run_id, self.turn_epoch, self.request_id


@dataclass(frozen=True)
class HostReadContext:
    """Short-lived, session-bound capability for snapshot and event reads only."""

    conversation_id: str
    active: bool = True


@dataclass(frozen=True)
class ReviewDisclosureDecision:
    """Trusted Host decision bound to one exact candidate review request."""

    approved: bool
    request_sha256: str
    approved_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.approved) is not bool:
            raise ValueError("review disclosure approval must be boolean")
        if not _is_sha256_hex(self.request_sha256):
            raise ValueError("review disclosure request digest is invalid")
        if type(self.approved_paths) is not tuple or any(type(path) is not str for path in self.approved_paths):
            raise ValueError("review disclosure paths must be a trusted tuple")


@dataclass(frozen=True)
class TrustedHumanMessageBinding:
    """Backend-issued proof of the exact raw human request bound to a live turn.

    The Host accepts this only from its trusted resolver, never from tool/model
    arguments. The resolver must read the original inbound request, not a model
    transcript or a caller-provided body.
    """

    context_identity: tuple[str, str, int, str]
    request_id: str
    message_id: str
    raw_text: str


class HostRunContextProvider:
    """A scoped backend capability; no process-global current-session slot."""

    def __init__(self) -> None:
        self._slot = ContextVar(f"self_evolution_run_{id(self)}", default=None)

    def __call__(self) -> HostRunContext | None:
        binding = self._slot.get()
        if binding is None:
            return None
        context, live, lease = binding
        try:
            if not lease[0] or live() is not True:
                return None
            if type(context) is HostRunContext and context.cancel_event.is_set():
                return None
            return context if type(context) in {HostRunContext, HostReadContext} and context.active else None
        except Exception:
            return None

    @contextmanager
    def bind(self, context: HostRunContext, live: Callable[[], bool]):
        if type(context) is not HostRunContext or not callable(live) or self._slot.get() is not None:
            raise ValueError("invalid or nested host run binding")
        lease = [True]
        token = self._slot.set((context, live, lease))
        try:
            yield
        finally:
            lease[0] = False
            self._slot.reset(token)

    @contextmanager
    def bind_read(self, conversation_id: str, live: Callable[[], bool]):
        """Bind one verified live connection to read-only Host operations.

        This context is intentionally not a HostRunContext, so start and every
        mutating Host operation continue to fail their exact-type/run checks.
        """
        if (type(conversation_id) is not str or not conversation_id or not callable(live)
                or self._slot.get() is not None):
            raise ValueError("invalid or nested host read binding")
        context = HostReadContext(conversation_id)
        lease = [True]
        token = self._slot.set((context, live, lease))
        try:
            yield
        finally:
            lease[0] = False
            self._slot.reset(token)


class _StartCancellation(threading.Event):
    """Latch parent cancellation until the worker has been handed off.

    The bridge event is reused by later chat turns, so it must not stay linked
    after handoff. A task-specific stop remains independently settable.
    """

    def __init__(self, parent: threading.Event) -> None:
        super().__init__()
        self._parent = parent
        self._parent_lock = threading.Lock()

    def is_set(self) -> bool:
        with self._parent_lock:
            if self._parent is not None and self._parent.is_set():
                super().set()
            return super().is_set()

    def detach_parent(self) -> bool:
        with self._parent_lock:
            if self._parent is not None and self._parent.is_set():
                super().set()
            self._parent = None
            return super().is_set()


@dataclass(frozen=True)
class TrustedWorkerCapabilities:
    """Explicit trusted worker dependency bundle; never populated from model/tool input."""

    client: ConfirmedToolClient
    model_id: str
    outbound_request_confirm_fn: Callable[[str, bool], str] | None
    tools: tuple[HostTool, ...]
    process_tree_stopped: Callable[[EvolutionIdentity], bool] | None
    max_requests: int = DEFAULT_MAX_REQUESTS
    enable_experimental_start: bool = False
    experimental_profile: str = "disabled"
    skills_root: Path | None = None
    authorized_skill_ids: tuple[str, ...] = ()
    # Not yet wired by production bootstrap. Until a trusted callback is injected,
    # the worker must not expose Skill installation as a capability.
    skill_install_approval_fn: Callable[[dict[str, Any]], bool] | None = None
    skill_install_provider: object | None = None

    def __post_init__(self) -> None:
        if (not callable(getattr(self.client, "chat_confirmed_tools", None))
                or not callable(getattr(self.client, "set_cancel_event", None))):
            raise ValueError("confirmed-tools client is required; bare chat is not supported")
        if type(self.model_id) is not str or not self.model_id.strip() or len(self.model_id) > 256:
            raise ValueError("trusted model_id is required")
        if self.outbound_request_confirm_fn is not None and not callable(self.outbound_request_confirm_fn):
            raise ValueError("trusted outbound confirmation callback must be callable")
        if (not isinstance(self.tools, tuple)
                or any(type(tool) is not HostTool for tool in self.tools)
                or len({tool.name for tool in self.tools}) != len(self.tools)):
            raise ValueError("explicit unique host tool closures are required")
        if self.process_tree_stopped is not None and not callable(self.process_tree_stopped):
            raise ValueError("trusted process-tree stop confirmer must be callable")
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= MAX_REQUESTS:
            raise ValueError("max_requests is out of range")
        if type(self.enable_experimental_start) is not bool:
            raise ValueError("enable_experimental_start must be a boolean")
        if type(self.experimental_profile) is not str or self.experimental_profile not in {"disabled", "static_ast"}:
            raise ValueError("unsupported self-evolution experimental profile")
        if self.experimental_profile == "static_ast":
            if not self.enable_experimental_start:
                raise ValueError("static_ast profile requires explicit experimental start opt-in")
            if self.tools:
                raise ValueError("static_ast profile does not accept arbitrary HostTools")
            if (self.skills_root is not None or self.authorized_skill_ids
                    or self.skill_install_approval_fn is not None or self.skill_install_provider is not None):
                raise ValueError("static_ast profile does not accept Skill capabilities")
        elif (not self.tools or not callable(self.outbound_request_confirm_fn)
              or not callable(self.process_tree_stopped)):
            raise ValueError("explicit unique host tools, outbound confirmation, and process proof are required")
        if self.skills_root is not None and not isinstance(self.skills_root, Path):
            raise ValueError("skills_root must be a host Path")
        if (type(self.authorized_skill_ids) is not tuple
                or any(type(item) is not str for item in self.authorized_skill_ids)):
            raise ValueError("authorized_skill_ids must be a host tuple")
        if self.skill_install_approval_fn is not None and not callable(self.skill_install_approval_fn):
            raise ValueError("skill install approval must be a trusted host callback")


@dataclass
class _TaskState:
    task_id: str
    context_identity: tuple[str, str, int, str]
    workspace_record: dict
    attribution_baseline: Baseline | None
    created_evidence: dict
    snapshot_review_baseline: snapshot_review.FrozenBaseline | None = None
    snapshot_tree_baseline: object | None = field(default=None, repr=False)
    verification_evidence: tuple[dict, ...] = ()
    identity: EvolutionIdentity | None = None
    cancel_event: threading.Event | None = None
    owner_id: str | None = None
    worker_thread: threading.Thread | None = None
    guidance_inbox: GuidanceInbox | None = None
    sequence: int = 0
    writer_held: bool = False
    # Host-owned verifier detail survives the worker's intentionally minimized result.
    check_evidence_provenance: dict[str, dict] = field(default_factory=dict, repr=False)
    verification_attempted: bool = False
    # In-process evidence only; invalidated on a new review or after one apply attempt.
    reviewed_content_binding: tuple[str, str, tuple[str, ...], str] | None = field(default=None, repr=False)
    per_start_outbound_approval_fn: Callable[[EvolutionIdentity, str], bool | str] | None = field(default=None, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class SelfEvolutionHost:
    """Host-owned coordinator. Public actions accept no paths, commands or hashes."""

    def __init__(
        self,
        config: SelfEvolutionHostConfig,
        *,
        integrity_key: bytes,
        context_provider: Callable[[], HostRunContext | None],
        trusted_verifier_resolver: Callable[[str, dict, str, str], WorkspaceBinding | None],
        store: EvolutionStore | None = None,
        worker_capabilities: TrustedWorkerCapabilities | None = None,
        workspace: SelfEvolutionWorkspace | None = None,
        paths: object | None = None,
        trusted_human_message_resolver: Callable[[HostRunContext], TrustedHumanMessageBinding | None] | None = None,
        trusted_review_disclosure_fn: Callable[[Mapping[str, object]], ReviewDisclosureDecision] | None = None,
        trusted_selected_review_paths_fn: Callable[[Mapping[str, object]], tuple[str, ...]] | None = None,
        trusted_native_apply_claim_fn: Callable[[object], object] | None = None,
    ) -> None:
        if type(config) is not SelfEvolutionHostConfig:
            raise ValueError("a fixed SelfEvolutionHostConfig is required")
        if not isinstance(integrity_key, bytes) or len(integrity_key) < 32:
            raise ValueError("persistent Host integrity key must contain at least 32 bytes")
        if not callable(context_provider):
            raise ValueError("trusted run resolver is required")
        if trusted_human_message_resolver is not None and not callable(trusted_human_message_resolver):
            raise ValueError("trusted human-message resolver must be callable")
        if trusted_review_disclosure_fn is not None and not callable(trusted_review_disclosure_fn):
            raise ValueError("trusted review disclosure callback must be callable")
        if trusted_selected_review_paths_fn is not None and not callable(trusted_selected_review_paths_fn):
            raise ValueError("trusted reviewed path selection callback must be callable")
        if trusted_native_apply_claim_fn is not None and not callable(trusted_native_apply_claim_fn):
            raise ValueError("trusted native apply claim callback must be callable")
        self._trusted_selected_review_paths_fn = trusted_selected_review_paths_fn
        self._trusted_native_apply_claim_fn = trusted_native_apply_claim_fn
        self._paths = paths
        self._config = config
        self._key = bytes(integrity_key)
        self._context_provider = context_provider
        self._trusted_verifier_resolver = trusted_verifier_resolver
        self._trusted_human_message_resolver = trusted_human_message_resolver
        self._trusted_review_disclosure_fn = trusted_review_disclosure_fn
        if store is not None and type(store) is not EvolutionStore:
            raise ValueError("trusted EvolutionStore instance is required")
        if worker_capabilities is not None and type(worker_capabilities) is not TrustedWorkerCapabilities:
            raise ValueError("trusted worker capability bundle is required")
        static_profile = bool(worker_capabilities is not None
                              and worker_capabilities.experimental_profile == "static_ast")
        if static_profile:
            from paths import AgentPaths
            if type(paths) is not AgentPaths:
                raise ValueError("static_ast profile requires trusted AgentPaths")
        elif paths is not None:
            from paths import AgentPaths
            if type(paths) is not AgentPaths:
                raise ValueError("trusted AgentPaths instance is required")
        if not callable(trusted_verifier_resolver) and not (
                static_profile and not config.verification_commands and trusted_verifier_resolver is None):
            raise ValueError("trusted verification resolver is required")
        if static_profile and config.verification_commands:
            raise ValueError("static_ast profile must not configure executable verification commands")
        if not static_profile and not config.verification_commands:
            raise ValueError("execution profile requires fixed verification commands")
        if static_profile and (not config.verification_source_paths
                               or any(not item.lower().endswith(".py")
                                      for item in config.verification_source_paths)):
            raise ValueError("static_ast profile requires a non-empty Python source allowlist")
        if static_profile and not set(config.verification_source_paths).issubset(config.attribution_paths):
            raise ValueError("static_ast Python sources must be inside the fixed attribution allowlist")
        if workspace is not None and type(workspace) is not SelfEvolutionWorkspace:
            raise ValueError("trusted SelfEvolutionWorkspace instance is required")
        if workspace is not None:
            if (Path(workspace._stable_repository) != config.stable_repository
                    or Path(workspace._trust_root) != config.trust_root
                    or not hmac.compare_digest(workspace._integrity_key, bytes(integrity_key))):
                raise ValueError("prepared workspace does not match the fixed Host roots and key")
        self._store = store
        self._worker_capabilities = worker_capabilities
        self._startup_reconciliation_error: str | None = None
        if self._store is not None:
            try:
                # Durable owners from a previous Host instance cannot be assumed dead
                # or stopped. Reconcile every legacy slot and active snapshot to
                # unknown after exposing any recovery read; never release ownership.
                self._store.reconcile_startup_writers()
            except StoreError:
                self._startup_reconciliation_error = "startup_reconciliation_unavailable"
        self._config_identity = self._digest_config()
        self._workspace = workspace or SelfEvolutionWorkspace(
            config.stable_repository, config.trust_root, self._key,
        )
        # Resolve once from trusted Host process configuration; never accept a
        # tool-supplied executable or materializer. Workspace reconstructs the
        # exact materializer class and rechecks its production safety gate.
        self._snapshot_git_executable = shutil.which("git")
        self._snapshot_materializer = (
            SafeSnapshotMaterializer(self._snapshot_git_executable)
            if self._snapshot_git_executable else None
        )
        self._tasks: dict[str, _TaskState] = {}
        self._tasks_lock = threading.RLock()
        self._verification = VerificationRunner(
            config.verification_commands,
            workspace_resolver=self._resolve_verification_binding,
        )

    @property
    def worker_bound(self) -> bool:
        """True only when optional worker dependencies were explicitly injected."""
        return self._store is not None and self._worker_capabilities is not None

    @property
    def production_ready(self) -> bool:
        """False until full-tree candidate identity and linked-worktree safety are established."""
        return False

    @property
    def full_candidate_coverage_available(self) -> bool:
        """Configured attribution/source paths are partial evidence, not a tree manifest."""
        return False

    @property
    def snapshot_candidate_creation_available(self) -> bool:
        """Enable only the ACL/identity/enrollment-checked Windows experiment lane."""
        return bool(self._static_ast_profile and os.name == "nt" and self._snapshot_materializer is not None)

    @property
    def _static_ast_profile(self) -> bool:
        caps = self._worker_capabilities
        return bool(caps is not None and caps.enable_experimental_start
                    and caps.experimental_profile == "static_ast")

    @property
    def os_sandboxed(self) -> bool:
        return False

    @property
    def merge_authorized(self) -> bool:
        return False

    @property
    def experimental_apply_target_ready(self) -> bool:
        """Revalidate that static_ast applies could target only an enrolled disposable repo."""
        if (not self._static_ast_profile or self._paths is None or self._store is None
                or os.name != "nt"):
            return False
        try:
            from self_evolution_backend import _validate_disposable_target
            stable = Path(self._config.stable_repository).resolve(strict=True)
            agent_root = Path(self._paths.agent_root).resolve(strict=True)
            storage_root = Path(self._store.path).parent.resolve(strict=True)
            trust_root = Path(self._config.trust_root).resolve(strict=True)
            _validate_disposable_target(stable, agent_root, storage_root, trust_root)
            return True
        except Exception:
            return False

    def start(
        self,
        request: EvolutionRequest,
        *,
        start_request_claim: StartRequestClaim | None = None,
        outbound_approval_fn: Callable[[EvolutionIdentity, str], bool | str] | None = None,
        on_attempt_created: Callable[[EvolutionIdentity], bool] | None = None,
    ) -> dict:
        """Persist and dispatch after host intent, claim, and per-start approval binding."""
        context, error = self._live_context(require_self_evolution_intent=True)
        if error:
            return _denied(error)
        assert context is not None
        if getattr(self, "_startup_reconciliation_error", None) is not None:
            return _denied("startup_state_unresolved")
        if context.self_evolution_intent is not True:
            return _denied("explicit_self_evolution_intent_required")
        if type(request) is not EvolutionRequest:
            return _denied("typed_user_request_required")
        if self._worker_capabilities is None:
            return _denied("worker_capabilities_unavailable")
        if not self._worker_capabilities.enable_experimental_start:
            return _denied("self_evolution_host_not_production_ready")
        if self._static_ast_profile and not self.snapshot_candidate_creation_available:
            return _denied("experimental_windows_snapshot_unavailable")
        if self._store is None:
            return _denied("persistent_store_unavailable")
        if (type(start_request_claim) is not StartRequestClaim or not start_request_claim.claim_token
                or start_request_claim.status != "claimed"
                or start_request_claim.session_id != context.conversation_id
                or start_request_claim.request_id != context.request_id):
            return _denied("persistent_start_claim_required")
        if not self.full_candidate_coverage_available and not self._static_ast_profile:
            # Do not create a candidate or start a writer using a digest of only
            # configured attribution paths as if it covered the entire worktree.
            return _denied("full_candidate_manifest_unavailable")
        # Confirmation is required after any candidate or durable task is created.
        if self._static_ast_profile:
            if not callable(outbound_approval_fn):
                return _denied("per_start_outbound_approval_required")
            if not callable(on_attempt_created):
                return _denied("attempt_registration_required")
        elif not callable(self._worker_capabilities.outbound_request_confirm_fn):
            return _denied("outbound_confirmation_required")
        elif outbound_approval_fn is not None or on_attempt_created is not None:
            return _denied("per_start_callbacks_not_available")
        # Consume the durable request reservation atomically before any candidate
        # side effect. A read/check followed by create would leave an owner TOCTOU.
        _, error = self._live_context(expected=context.identity(), require_self_evolution_intent=True)
        if error:
            return _denied(error)
        try:
            self._store.consume_start_request_claim(
                start_request_claim,
                session_id=context.conversation_id,
                run_id=context.run_id,
                turn_epoch=context.turn_epoch,
            )
        except StoreOwnershipError:
            # A replay, stale claim, or competing/unknown owner is a definitive
            # rejection. Do not create a candidate or disturb another owner.
            return _denied("persistent_start_claim_rejected")
        except Exception:
            # The consume commit may have succeeded even if its response was
            # lost. Preserve the request as ambiguous; no workspace side effect
            # has happened yet and the caller must not retry it automatically.
            return _unknown("persistent_start_claim_unconfirmed")
        _, error = self._live_context(expected=context.identity(), require_self_evolution_intent=True)
        if error:
            return _unknown(error)
        created = self.create(expected_context_identity=context.identity(),
                              require_self_evolution_intent=True)
        if created.get("status") != "created":
            return created
        task_id = created["task_id"]
        # Candidate creation may be slow; revocation during it must never launch
        # the worker. Retain the candidate rather than guessing it is disposable.
        _, error = self._live_context(expected=context.identity(), require_self_evolution_intent=True)
        if error:
            return _unknown(error, task_id=task_id, candidate=created.get("candidate"))
        with self._tasks_lock:
            state = self._tasks.get(task_id)
        if state is None:
            return _unknown("host_task_state_missing", task_id=task_id)
        if (state.workspace_record.get("candidate_kind") == "materialized_snapshot"
                and state.snapshot_review_baseline is None):
            return _unknown("snapshot_review_baseline_unavailable", task_id=task_id,
                            candidate=state.created_evidence)
        record, failure = self._inspect_bound_record(state)
        if failure or record is None:
            return _unknown(failure or "candidate_unavailable", task_id=task_id,
                             candidate=state.created_evidence)
        owner_id: str | None = None
        identity: EvolutionIdentity | None = None
        try:
            if record.get("candidate_kind") == "materialized_snapshot":
                candidate = CandidateBinding(
                    candidate_id=task_id,
                    stable_commit=record["stable_head"],
                    git_common_id=None,
                    stable_worktree_id=None,
                    candidate_worktree_id=None,
                    baseline_manifest_digest=record["baseline_manifest_digest"],
                    candidate_kind="materialized_snapshot",
                    snapshot_root=record["candidate_path"],
                    snapshot_tree_oid=record["binding"]["tree_oid"],
                    snapshot_binding_digest=_snapshot_binding_digest(record["binding"]),
                )
            else:
                candidate = CandidateBinding(
                    candidate_id=task_id,
                    stable_commit=record["stable_head"],
                    git_common_id=record["stable_repository"]["git_common_dir"],
                    stable_worktree_id=record["stable_repository"]["git_dir"],
                    candidate_worktree_id=record["candidate_repository"]["git_dir"],
                    baseline_manifest_digest=record["baseline_manifest_digest"],
                )
            if self._static_ast_profile:
                frozen = state.snapshot_review_baseline
                if frozen is None or not frozen.full_tree:
                    raise ValueError("full frozen candidate baseline unavailable")
                candidate_digest = frozen.baseline_digest
                initial = snapshot_review._capture(record["candidate_path"])
                if (initial.root_identity != frozen.root_identity
                        or initial.digest != candidate_digest or not initial.full_candidate_coverage
                        or initial.excluded_metadata):
                    raise ValueError("candidate changed after frozen baseline")
            else:
                baseline_evidence = check_changes(state.attribution_baseline) if state.attribution_baseline else None
                if not baseline_evidence or not baseline_evidence.get("safe_to_consider"):
                    raise ValueError("candidate baseline cannot be safely bound")
                candidate_digest = baseline_evidence["summary_sha256"]
            attempt_id = str(uuid.uuid4())
            identity = EvolutionIdentity(context.conversation_id, task_id, attempt_id,
                                         context.run_id, context.turn_epoch)
            snapshot = EvolutionSnapshot(identity=identity, phase="preparing", snapshot_revision=1,
                                         candidate=candidate, candidate_digest=candidate_digest,
                                         allowed_next_actions=("cancel", "get_snapshot"))
            owner_id = str(uuid.uuid4())
            _, error = self._live_context(expected=context.identity(), require_self_evolution_intent=True)
            if error:
                return _unknown(error, task_id=task_id, candidate=state.created_evidence)
            self._store.create_task_with_writer(identity, snapshot, owner_id,
                                                start_request_claim=start_request_claim)
            state.owner_id = owner_id
            state.writer_held = True
        except Exception:
            # create_task_with_writer is atomic, but an exception after commit is
            # still ambiguous to this caller. Quarantine only this call's exact
            # owner token; if it never committed, the store changes nothing.
            if owner_id is not None and identity is not None:
                try:
                    quarantined = self._store.mark_orphaned_unknown(owner_id)
                    if task_id in quarantined:
                        state.identity = identity
                        state.owner_id = owner_id
                        state.writer_held = True
                        state.sequence = 2
                except Exception:
                    pass
            return _unknown("persistent_task_or_writer_claim_unconfirmed", task_id=task_id,
                            candidate=state.created_evidence)
        cancel_event = _StartCancellation(context.cancel_event)
        state.identity = identity
        state.cancel_event = cancel_event
        state.guidance_inbox = GuidanceInbox((identity.session_id, identity.task_id, identity.attempt_id))
        state.owner_id = owner_id
        state.per_start_outbound_approval_fn = outbound_approval_fn
        # Register the attempt with the live server connection before worker
        # dispatch. Failure leaves the durable owner quarantined, never unclaimed.
        if self._static_ast_profile:
            try:
                registered = on_attempt_created(identity)  # type: ignore[misc]
            except Exception:
                registered = False
            if registered is not True:
                cancel_event.detach_parent()
                self._mark_unknown(state, "attempt_registration_unconfirmed")
                return _unknown("attempt_registration_unconfirmed", task_id=task_id,
                                candidate=state.created_evidence)
        # create_task_with_writer committed task_accepted at sequence 1 together
        # with the task and writer. The first subsequent transition is sequence 2.
        state.sequence = 1
        task = request.user_goal
        if request.acceptance_points:
            task += "\n\nAcceptance points:\n" + "\n".join(f"- {item}" for item in request.acceptance_points)
        if request.context_refs:
            task += "\n\nHost-authorized context references (resolve only through host tools):\n" + "\n".join(request.context_refs)
        # The durable writer claim may already exist: quarantine it rather than
        # releasing ownership when the authorizing turn was revoked in the gap.
        _, error = self._live_context(expected=context.identity(), require_self_evolution_intent=True)
        if error:
            cancel_event.detach_parent()
            self._mark_unknown(state, error)
            return _unknown(error, task_id=task_id, candidate=state.created_evidence)
        thread = threading.Thread(target=self._run_worker, args=(state, task),
                                  name=f"self-evolution-{task_id[:8]}", daemon=True)
        state.worker_thread = thread
        _, error = self._live_context(expected=context.identity(), require_self_evolution_intent=True)
        if error:
            state.worker_thread = None
            cancel_event.detach_parent()
            self._mark_unknown(state, error)
            return _unknown(error, task_id=task_id, candidate=state.created_evidence)
        try:
            thread.start()
        except BaseException:
            cancel_event.detach_parent()
            self._mark_unknown(state, "worker_start_unconfirmed")
            return _unknown("worker_start_unconfirmed", task_id=task_id,
                            candidate=state.created_evidence)
        if cancel_event.detach_parent():
            # A live worker may already exist. Signal it and quarantine ownership;
            # this is not proof the Python thread or any descendants have stopped.
            self._mark_unknown(state, "cancelled_during_start")
            return _unknown("cancelled_during_start", task_id=task_id,
                            candidate=state.created_evidence)
        return {"status": "started", "task_id": task_id, "attempt_id": attempt_id,
                "run_id": identity.run_id, "worker_bound": True, "os_sandbox": False,
                "merge_authorized": False}

    def get_snapshot(self, task_id: str, *, session_id: str | None = None) -> EvolutionSnapshot | None:
        if self._store is None or self._startup_reconciliation_error is not None:
            return None
        try:
            context = self._context_provider()
        except Exception:
            return None
        if type(context) not in {HostRunContext, HostReadContext} or not context.active:
            return None
        # session_id is a caller assertion only; it must equal the authenticated host session.
        authenticated_session = context.conversation_id
        if session_id is not None and session_id != authenticated_session:
            return None
        try:
            snapshot = self._store.read_task(authenticated_session, task_id)
            if (snapshot is None or snapshot.identity.session_id != authenticated_session
                    or snapshot.identity.task_id != task_id):
                return None
            if not self._persisted_candidate_binding_matches(snapshot):
                return None
            with self._tasks_lock:
                local = self._tasks.get(task_id)
            active_phases = {"preparing", "observing", "awaiting_design_approval", "editing", "checking", "cancelling"}
            if snapshot.phase in active_phases and (local is None or local.identity != snapshot.identity):
                # Reads must never reconcile an active row: a concurrent Host may
                # own its writer even if this instance passed startup checks after
                # the row appeared. Only an owner-bound Store transition may mutate it.
                return None
            return snapshot if self._read_context_still_matches(context) else None
        except StoreError:
            return None

    def get_event_page(self, task_id: str, attempt_id: str, *, after_sequence: int = 0,
                       session_id: str | None = None) -> EventPage | None:
        """Return an authenticated bounded event window with explicit gap facts."""
        if self._store is None or self._startup_reconciliation_error is not None:
            return None
        try:
            context = self._context_provider()
        except Exception:
            return None
        if type(context) not in {HostRunContext, HostReadContext} or not context.active:
            return None
        authenticated_session = context.conversation_id
        if session_id is not None and session_id != authenticated_session:
            return None
        try:
            snapshot = self._store.read_task(authenticated_session, task_id)
            if snapshot is None or snapshot.identity.attempt_id != attempt_id:
                return None
            page = self._store.read_event_page(authenticated_session, task_id, attempt_id,
                                               after_sequence=after_sequence)
            return page if self._read_context_still_matches(context) else None
        except StoreError:
            return None

    def _read_context_still_matches(self, context: HostRunContext | HostReadContext) -> bool:
        try:
            current = self._context_provider()
        except Exception:
            return False
        if type(current) is not type(context) or not current.active:
            return False
        if type(context) is HostReadContext:
            return current is context
        return (current.identity() == context.identity()
                and current.mode == context.mode
                and current.work_intent == context.work_intent
                and current.request_origin == context.request_origin
                and not current.cancel_event.is_set())

    def resolve_unique_active_task(self) -> tuple[str, str] | None:
        """Resolve exactly one cancellable task in the authenticated live session."""
        context, error = self._live_context()
        if error or context is None or context.request_origin != "human":
            return None
        active_phases = {"preparing", "observing", "awaiting_design_approval", "editing",
                         "checking", "blocked", "cancelling"}
        matches: list[tuple[str, str]] = []
        with self._tasks_lock:
            states = tuple(self._tasks.values())
        for state in states:
            identity = state.identity
            if identity is None or identity.session_id != context.conversation_id:
                continue
            try:
                snapshot = self._store.read_task(context.conversation_id, state.task_id)
            except Exception:
                return None
            if (snapshot is None or snapshot.identity != identity or snapshot.phase not in active_phases
                    or state.cancel_event is None or state.worker_thread is None
                    or not state.worker_thread.is_alive()):
                continue
            matches.append((identity.task_id, identity.attempt_id))
            if len(matches) > 1:
                return None
        if not self._read_context_still_matches(context):
            return None
        return matches[0] if len(matches) == 1 else None

    def request_cancel(self, task_id: str, attempt_id: str) -> CancelReceipt:
        if self._store is None:
            return CancelReceipt(task_id or "unavailable", attempt_id or "unavailable", "unknown")
        context, error = self._live_context()
        if error or context is None or context.request_origin != "human":
            return CancelReceipt(task_id or "unavailable", attempt_id or "unavailable", "unknown")
        try:
            snapshot = self._store.read_task(context.conversation_id, task_id)
            if snapshot is None or snapshot.identity.attempt_id != attempt_id:
                return CancelReceipt(task_id, attempt_id, "not_found")
            if snapshot.phase in {"cancelled", "failed", "merged", "awaiting_review"}:
                return CancelReceipt(task_id, attempt_id, "already_finished")
            with self._tasks_lock:
                state = self._tasks.get(task_id)
            if state is None or state.identity != snapshot.identity or state.cancel_event is None:
                return CancelReceipt(task_id, attempt_id, "unknown")
            with state.lock:
                # Signal cancellation after persistence so a slow/failing write cannot
                # leave an otherwise cancellable provider request running.
                state.cancel_event.set()
                if state.guidance_inbox is not None:
                    state.guidance_inbox.close("cancelled")
                if snapshot.phase != "cancelling":
                    changed = replace(snapshot, phase="cancelling", snapshot_revision=snapshot.snapshot_revision + 1,
                                      allowed_next_actions=("get_snapshot",))
                    sequence = state.sequence + 1
                    event = EvolutionEvent(snapshot.identity, sequence, "cancelling", "phase_changed", (),
                                           changed.snapshot_revision)
                    self._store.commit_transition(snapshot.identity, snapshot.snapshot_revision, changed, event)
                    state.sequence = sequence
            return CancelReceipt(task_id, attempt_id, "accepted")
        except Exception:
            # Persistence, protocol, and storage-engine failures are all fail-closed.
            return CancelReceipt(task_id or "unavailable", attempt_id or "unavailable", "unknown")

    def receive_worker_guidance(self, task_id: str, attempt_id: str) -> dict:
        """Accept only backend-bound raw user input for the current request.

        No message body or ID is accepted from the caller. The trusted backend
        resolver is the sole source of original human text and its identifiers.
        The locked recheck narrows stale-context races, but context revocation is
        not linearized with this state lock; the backend must serialize revocation
        and message-binding issuance on a shared lease/lock after production use.
        """
        context, error = self._live_context()
        if error or context is None or context.request_origin != "human":
            return {"status": "denied", "reason": error or "human_request_required"}
        if self._trusted_human_message_resolver is None:
            return {"status": "denied", "reason": "human_message_binding_unavailable"}
        if (type(task_id) is not str or not task_id or type(attempt_id) is not str
                or not attempt_id):
            return {"status": "denied", "reason": "task_attempt_binding_mismatch"}
        with self._tasks_lock:
            state = self._tasks.get(task_id)
        if (state is None or state.identity is None or state.guidance_inbox is None
                or state.identity.session_id != context.conversation_id
                or state.identity.attempt_id != attempt_id):
            return {"status": "denied", "reason": "task_attempt_binding_mismatch"}
        try:
            binding = self._trusted_human_message_resolver(context)
        except Exception:
            binding = None
        if (type(binding) is not TrustedHumanMessageBinding
                or binding.context_identity != context.identity()
                or binding.request_id != context.request_id
                or type(binding.message_id) is not str or not binding.message_id.strip()
                or type(binding.raw_text) is not str or not binding.raw_text.strip()):
            return {"status": "denied", "reason": "human_message_binding_invalid"}
        with state.lock:
            # Re-resolve after waiting for the task lock. This does not itself
            # make backend context revocation atomic with enqueue; see docstring.
            current, current_error = self._live_context(expected=context.identity())
            if current_error or current is None or current.identity() != context.identity():
                return {"status": "denied", "reason": current_error or "execution_binding_mismatch"}
            if (state.identity is None or state.identity.session_id != current.conversation_id
                    or state.identity.task_id != task_id or state.identity.attempt_id != attempt_id):
                return {"status": "denied", "reason": "task_attempt_binding_mismatch"}
            if state.cancel_event is None or state.cancel_event.is_set():
                return {"status": "closed", "reason": "task_cancelled"}
            if not self._worker_binding_valid(state):
                return {"status": "closed", "reason": "worker_task_not_live"}
            if (binding.context_identity != current.identity()
                    or binding.request_id != current.request_id):
                return {"status": "denied", "reason": "human_message_binding_invalid"}
            receipt = state.guidance_inbox.receive(
                (current.conversation_id, task_id, attempt_id), binding.message_id, binding.raw_text,
            )
        return {"status": receipt.status, "message_id": receipt.message_id,
                "delivery": receipt.delivery_status}

    def get_worker_guidance_status(self, task_id: str, attempt_id: str) -> dict:
        """Read delivery states without exposing the untrusted message bodies."""
        context, error = self._live_context()
        if error or context is None or context.request_origin != "human":
            return {"status": "denied", "reason": error or "human_request_required"}
        if type(task_id) is not str or type(attempt_id) is not str:
            return {"status": "denied", "reason": "task_attempt_binding_mismatch"}
        with self._tasks_lock:
            state = self._tasks.get(task_id)
        if (state is None or state.identity is None or state.guidance_inbox is None
                or state.identity.session_id != context.conversation_id
                or state.identity.attempt_id != attempt_id):
            return {"status": "denied", "reason": "task_attempt_binding_mismatch"}
        return {"status": "ok", "messages": [
            {"message_id": item.message_id, "delivery": item.status}
            for item in state.guidance_inbox.snapshot()
        ]}

    def wait_for_worker(self, task_id: str, attempt_id: str, *, timeout: float | None = None) -> bool:
        """Host/test lifecycle hook; True means the Python worker thread returned, not tree stop."""
        with self._tasks_lock:
            state = self._tasks.get(task_id)
        if state is None or state.identity is None or state.identity.attempt_id != attempt_id:
            return False
        thread = state.worker_thread
        if thread is None:
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def writer_held(self, task_id: str) -> bool:
        """Non-authoritative diagnostic used by host tests; never releases ownership."""
        with self._tasks_lock:
            state = self._tasks.get(task_id)
        return bool(state and state.writer_held)

    def _per_start_outbound_confirm(self, state: _TaskState) -> Callable[[str, bool], str]:
        identity = state.identity
        approve = state.per_start_outbound_approval_fn
        if identity is None or not callable(approve):
            return lambda _preview, _allow_all: "n"

        def confirm(preview: str, allow_all: bool) -> str:
            if (type(allow_all) is not bool or allow_all is not False
                    or type(preview) is not str or not preview.strip()
                    or len(preview) > 32_000 or state.identity != identity
                    or not self._worker_binding_valid(state)):
                return "n"
            try:
                decision = approve(identity, preview)
            except Exception:
                return "n"
            if decision is True or decision == "y" and type(decision) is str:
                return "y"
            if decision is False or decision == "n" and type(decision) is str:
                return "n"
            return "n"
        return confirm

    def _run_worker(self, state: _TaskState, task: str) -> None:
        assert self._store is not None and self._worker_capabilities is not None
        assert state.identity is not None and state.cancel_event is not None and state.owner_id is not None
        identity = state.identity
        caps = self._worker_capabilities
        try:
            digest = self._current_candidate_digest(state)
            if digest is None:
                self._mark_unknown(state, "candidate_digest_unavailable")
                return
            if self._static_ast_profile:
                worker_skills_root, worker_skill_ids = None, ()
            else:
                worker_skills_root, worker_skill_ids = self._worker_skill_scope(state, caps)
            worker_context = SelfEvolutionWorkerContext(
                session_id=identity.session_id, task_id=identity.task_id, attempt_id=identity.attempt_id,
                run_id=identity.run_id, turn_epoch=identity.turn_epoch,
                candidate_id=self._candidate_identity(state.workspace_record), candidate_digest=digest,
                cancel_event=state.cancel_event, model_id=caps.model_id,
                outbound_request_confirm_fn=(
                    self._per_start_outbound_confirm(state) if self._static_ast_profile
                    else caps.outbound_request_confirm_fn
                ),
                skills_root=worker_skills_root,
                authorized_skill_ids=worker_skill_ids,
                skill_worker_task_id=_skill_worker_task_id(
                    identity.session_id, identity.task_id, identity.attempt_id,
                    identity.run_id, identity.turn_epoch,
                ) if worker_skills_root is not None else "",
                after_each=lambda: self._worker_binding_valid(state),
                current_candidate_digest=lambda: self._current_candidate_digest(state),
                claim_guidance=lambda: self._claim_worker_guidance(state),
                resolve_guidance=lambda token, outcome: self._resolve_worker_guidance(state, token, outcome),
            )
            configured_tools = caps.tools
            if state.workspace_record.get("candidate_kind") == "materialized_snapshot":
                dynamic = build_self_evolution_candidate_tools(
                    resolve_candidate_root=self._resolve_worker_candidate_root,
                    validate_binding=self._validate_worker_candidate_root,
                    verification_runner=self._verification,
                    registered_check_ids=(tuple(self._config.verification_commands)
                                          if not self._static_ast_profile else ()),
                    check_timeout_seconds=float(self._config.verification_timeout_seconds),
                    include_verification=not self._static_ast_profile,
                )
                if self._static_ast_profile:
                    if caps.tools or not state.snapshot_review_baseline or not state.snapshot_review_baseline.full_tree:
                        self._mark_unknown(state, "experimental_worker_scope_unavailable")
                        return
                    static_check = HostTool(
                        "run_registered_check", "Run the host static_ast check; this parses allowlisted Python source only.",
                        lambda ctx, args: self._run_static_ast_check(state, ctx, args),
                    )
                    configured_tools = tuple(tool for tool in dynamic
                                             if tool.name != "run_registered_check") + (static_check,)
                else:
                    dynamic_names = {tool.name for tool in dynamic}
                    configured_tools = tuple(tool for tool in caps.tools if tool.name not in dynamic_names) + dynamic
                    skill_tool = self._build_worker_skill_install_tool(
                        state, worker_context, approval=caps.skill_install_approval_fn,
                        provider=caps.skill_install_provider,
                    )
                    if skill_tool is not None:
                        configured_tools = tuple(tool for tool in configured_tools
                                                 if tool.name != skill_tool.name) + (skill_tool,)
            tools = tuple(HostTool(tool.name, tool.description,
                                   lambda ctx, args, original=tool: self._invoke_bound_tool(state, ctx, original, args))
                          for tool in configured_tools)
            result = run_self_evolution_worker(task, client=caps.client, context=worker_context,
                                               tools=tools, max_requests=caps.max_requests,
                                               experimental_profile=(caps.experimental_profile
                                                                     if self._static_ast_profile else "disabled"),
                                               on_event=lambda phase, _payload: self._persist_worker_phase(state, phase))
            if state.guidance_inbox is not None:
                state.guidance_inbox.close(result.status)
            stopped = False
            try:
                if (self._static_ast_profile and not state.verification_attempted
                        and all(item.get("verification_type") == "static_ast"
                                for item in state.check_evidence_provenance.values())):
                    stopped = True
                elif callable(caps.process_tree_stopped):
                    stopped = caps.process_tree_stopped(identity) is True
            except Exception:
                stopped = False
            if result.status == "cancelled" and not stopped:
                self._mark_unknown(state, "process_tree_stop_unconfirmed")
                return
            if not stopped:
                self._mark_unknown(state, "worker_stop_unconfirmed")
                return
            self._settle_worker_result(state, result, stopped_confirmed=True)
        except BaseException:
            # Keep the durable writer slot on every unclassified escape.
            if state.guidance_inbox is not None:
                state.guidance_inbox.close("worker_exception_or_interruption")
            self._mark_unknown(state, "worker_exception_or_interruption")

    def _claim_worker_guidance(self, state: _TaskState):
        if (state.identity is None or state.cancel_event is None or state.cancel_event.is_set()
                or state.guidance_inbox is None or not self._worker_binding_valid(state)):
            return None
        return state.guidance_inbox.claim(
            (state.identity.session_id, state.identity.task_id, state.identity.attempt_id),
        )

    @staticmethod
    def _resolve_worker_guidance(state: _TaskState, token: str, outcome: str) -> bool:
        inbox = state.guidance_inbox
        if inbox is None or outcome not in {"delivered", "unknown", "failed"}:
            return False
        return inbox.resolve(token, outcome)

    def _worker_task_state(self, context: SelfEvolutionWorkerContext) -> _TaskState | None:
        if type(context) is not SelfEvolutionWorkerContext:
            return None
        with self._tasks_lock:
            state = self._tasks.get(context.task_id)
        identity = state.identity if state is not None else None
        if (identity is None or identity.session_id != context.session_id
                or identity.task_id != context.task_id or identity.attempt_id != context.attempt_id
                or identity.run_id != context.run_id or identity.turn_epoch != context.turn_epoch):
            return None
        return state

    def _resolve_worker_candidate_root(self, context: SelfEvolutionWorkerContext) -> str | None:
        state = self._worker_task_state(context)
        if state is None or state.workspace_record.get("candidate_kind") != "materialized_snapshot":
            return None
        record, failure = self._inspect_bound_record(state)
        if failure or record is None or record.get("candidate_kind") != "materialized_snapshot":
            return None
        if context.candidate_id != self._candidate_identity(record):
            return None
        return record["candidate_path"]

    def _validate_worker_candidate_root(self, context: SelfEvolutionWorkerContext,
                                        root: Path) -> bool:
        state = self._worker_task_state(context)
        if state is None or state.workspace_record.get("candidate_kind") != "materialized_snapshot":
            return False
        record, failure = self._inspect_bound_record(state)
        if failure or record is None:
            return False
        if context.candidate_id != self._candidate_identity(record):
            return False
        try:
            expected = Path(record["candidate_path"]).resolve(strict=True)
            actual = Path(root).resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        return (actual == expected
                and actual != Path(self._config.stable_repository).resolve(strict=True))

    def _worker_skill_scope(self, state: _TaskState, caps: TrustedWorkerCapabilities) -> tuple[Path | None, tuple[str, ...]]:
        """Bind Skill discovery to this candidate without widening Host grants."""
        if state.workspace_record.get("candidate_kind") != "materialized_snapshot":
            return caps.skills_root, caps.authorized_skill_ids
        record, failure = self._inspect_bound_record(state)
        if failure or record is None or record.get("candidate_kind") != "materialized_snapshot":
            return None, ()
        try:
            candidate_root = Path(record["candidate_path"]).resolve(strict=True)
            if candidate_root != Path(state.workspace_record["candidate_path"]).resolve(strict=True):
                return None, ()
            evolve_root = candidate_root / "evolve"
            skills_root = evolve_root / "skills"
            import stat
            for directory in (evolve_root, skills_root):
                info = directory.lstat()
                if (stat.S_ISLNK(info.st_mode)
                        or bool(getattr(info, "st_file_attributes", 0)
                                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
                        or not stat.S_ISDIR(info.st_mode)
                        or not directory.resolve(strict=True).is_relative_to(candidate_root)):
                    return None, ()
            from skill_runtime import normalize_host_authorized_skill_ids

            normalized = normalize_host_authorized_skill_ids(skills_root, caps.authorized_skill_ids)
            # The registry may discover newly installed IDs, but this worker only
            # receives the exact Host grant IDs that were already authorized.
            granted = frozenset(caps.authorized_skill_ids)
            return skills_root, tuple(skill_id for skill_id in normalized if skill_id in granted)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            return None, ()

    def _build_worker_skill_install_tool(
        self, state: _TaskState, context: SelfEvolutionWorkerContext, *,
        approval: Callable[[dict[str, Any]], bool] | None, provider: object | None = None,
    ) -> HostTool | None:
        """Expose only a trusted approval-provider-backed installer for this candidate.

        ``outbound_request_confirm_fn`` authorizes model egress, not Skill/file
        installation. Production bootstrap does not yet provide a Skill approval
        adapter, so an absent callback keeps this capability unavailable.
        """
        caps = self._worker_capabilities
        if state.identity is None or caps is None or not callable(approval):
            return None

        def invoke(ctx: SelfEvolutionWorkerContext, arguments: Mapping[str, object]) -> Mapping[str, object]:
            import json
            import re
            import stat
            from pathlib import PurePosixPath

            if (ctx is not context or ctx.task_id != state.task_id
                    or ctx.attempt_id != state.identity.attempt_id
                    or self._worker_task_state(ctx) is not state
                    or not self._worker_binding_valid(state)):
                return {"status": "blocked", "error_code": "candidate_binding_invalid"}
            try:
                source = arguments.get("source_request")
                if type(source) is not dict or len(json.dumps(source, ensure_ascii=False)) > 256 * 1024:
                    return {"status": "rejected", "error_code": "invalid_source_request"}
                source_kind = source.get("kind")
                if source_kind not in {"content", "github", "https"}:
                    return {"status": "rejected", "error_code": "unsupported_source_kind"}
                if any(key in source for key in ("target", "target_path", "skills_root", "destination", "install_path")):
                    return {"status": "rejected", "error_code": "model_target_not_allowed"}
                raw_root = self._resolve_worker_candidate_root(ctx)
                if raw_root is None:
                    return {"status": "blocked", "error_code": "candidate_unavailable"}
                candidate_root = Path(raw_root).resolve(strict=True)
                stable_root = Path(self._config.stable_repository).resolve(strict=True)
                if (candidate_root == stable_root or candidate_root in stable_root.parents
                        or stable_root in candidate_root.parents
                        or not self._validate_worker_candidate_root(ctx, candidate_root)):
                    return {"status": "blocked", "error_code": "candidate_binding_invalid"}

                # Require an existing task-local root. Do not mkdir through an
                # untrusted candidate component. lstat/resolution checks reject
                # observed links/reparse points, but cannot make path use atomic
                # against a concurrent filesystem swap (no OS sandbox is claimed).
                evolve_root = candidate_root / "evolve"
                skills_root = evolve_root / "skills"
                for directory in (evolve_root, skills_root):
                    try:
                        info = directory.lstat()
                    except OSError:
                        return {"status": "blocked", "error_code": "candidate_skills_root_invalid"}
                    attributes = getattr(info, "st_file_attributes", 0)
                    if (stat.S_ISLNK(info.st_mode)
                            or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
                            or not stat.S_ISDIR(info.st_mode)):
                        return {"status": "blocked", "error_code": "candidate_skills_root_invalid"}
                    try:
                        resolved_directory = directory.resolve(strict=True)
                    except (OSError, RuntimeError, ValueError):
                        return {"status": "blocked", "error_code": "candidate_skills_root_invalid"}
                    if not resolved_directory.is_relative_to(candidate_root):
                        return {"status": "blocked", "error_code": "candidate_skills_root_invalid"}
                if not self._validate_worker_candidate_root(ctx, candidate_root):
                    return {"status": "blocked", "error_code": "candidate_binding_invalid"}

                from skill_install import SkillInstallError, prepare_skill_install, commit_skill_install

                preview = prepare_skill_install(skills_root=skills_root, source=source, provider=provider)
                skill_id = getattr(preview, "skill_id", None)
                target = getattr(preview, "target", None)
                files = getattr(preview, "files", None)
                content_digest = getattr(preview, "content_sha256", None)
                if (type(skill_id) is not str or re.fullmatch(r"[a-z][a-z0-9-]{1,63}", skill_id) is None
                        or type(target) is not str or not isinstance(files, (tuple, list))
                        or len(files) > 512 or type(content_digest) is not str
                        or re.fullmatch(r"[0-9a-f]{64}", content_digest) is None):
                    return {"status": "rejected", "error_code": "invalid_install_preview"}
                expected_target = skills_root.resolve(strict=True) / skill_id
                if (Path(target).resolve(strict=False) != expected_target
                        or not expected_target.is_relative_to(skills_root.resolve(strict=True))):
                    return {"status": "rejected", "error_code": "install_target_outside_candidate"}
                for item in files:
                    relative = getattr(item, "path", None)
                    digest = getattr(item, "sha256", None)
                    if (type(relative) is not str or not relative or "\\" in relative
                            or PurePosixPath(relative).is_absolute()
                            or any(part in {"", ".", ".."} for part in relative.split("/"))
                            or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                        return {"status": "rejected", "error_code": "invalid_install_preview"}

                def binding_check() -> bool:
                    try:
                        live_root = self._resolve_worker_candidate_root(ctx)
                        return bool(
                            self._worker_task_state(ctx) is state
                            and self._worker_binding_valid(state)
                            and live_root is not None
                            and Path(live_root).resolve(strict=True) == candidate_root
                            and self._validate_worker_candidate_root(ctx, candidate_root)
                            and ctx.candidate_id == self._candidate_identity(state.workspace_record)
                        )
                    except Exception:
                        return False

                try:
                    expected_approval_payload, expected_approval_digest = _validated_skill_install_approval_payload(preview)
                except Exception:
                    return {"status": "rejected", "error_code": "invalid_install_preview"}

                def approve_bound(shown_payload: dict) -> bool:
                    # skill_install.commit_skill_install supplies approval_payload(),
                    # not its internal preview object. Bind the human decision to
                    # the complete exact payload, then re-check the live candidate.
                    if (type(shown_payload) is not dict or shown_payload != expected_approval_payload
                            or not hmac.compare_digest(_approval_payload_digest(shown_payload), expected_approval_digest)
                            or not binding_check()):
                        return False
                    try:
                        approved = approval(shown_payload) is True
                    except Exception:
                        return False
                    return approved and binding_check()

                if not binding_check():
                    return {"status": "blocked", "error_code": "candidate_binding_invalid"}
                result = commit_skill_install(preview, approval=approve_bound, binding_check=binding_check)
                result_status = getattr(result, "status", None)
                result_skill_id = getattr(result, "skill_id", skill_id)
                discovered = getattr(result, "discovered", False) is True
                residual = getattr(result, "residual", False) is True
                if result_skill_id != skill_id:
                    return {"status": "failed", "error_code": "install_result_skill_mismatch"}
                if result_status == "installed" and discovered:
                    return {"status": "installed", "skill_id": skill_id,
                            "discovered": True, "published": True, "residual": residual,
                            "content_sha256": content_digest,
                            "permission_grants_changed": False}
                if result_status in {
                    "installed_stage_cleanup_failed", "installed_refresh_failed", "installed_not_discoverable",
                }:
                    # These service outcomes mean publication already happened.
                    # Preserve the exact state; do not misreport it as an install
                    # failure or discard the installed candidate package.
                    warning = {
                        "installed_stage_cleanup_failed": "stage_cleanup_failed",
                        "installed_refresh_failed": "registry_refresh_failed",
                        "installed_not_discoverable": "skill_not_discoverable",
                    }[result_status]
                    return {"status": result_status, "skill_id": skill_id,
                            "discovered": discovered, "published": True, "residual": residual,
                            "warning_code": warning, "content_sha256": content_digest,
                            "permission_grants_changed": False}
                return {"status": "failed", "error_code": "install_not_confirmed"}
            except Exception as exc:
                if type(exc).__name__ == "SkillInstallError":
                    message = str(exc).casefold()
                    return {"status": "rejected" if "not approved" in message else "failed",
                            "error_code": "approval_rejected" if "not approved" in message else "skill_install_failed"}
                return {"status": "failed", "error_code": "skill_installer_unavailable_or_failed"}

        return HostTool(
            "install_candidate_skill",
            "Create or import a Skill package into this task candidate. Host preview approval is required; this grants no tools.",
            invoke,
        )

    def _persisted_candidate_binding_matches(self, snapshot: EvolutionSnapshot) -> bool:
        candidate = snapshot.candidate
        if candidate is None or candidate.candidate_kind == "git_worktree":
            return True
        if (candidate.candidate_kind != "materialized_snapshot"
                or candidate.candidate_id != snapshot.identity.task_id):
            return False
        try:
            record = self._inspect_snapshot_record(snapshot.identity.task_id)
        except Exception:
            return False
        return (
            candidate.stable_commit == record["stable_head"]
            and candidate.baseline_manifest_digest == record["baseline_manifest_digest"]
            and candidate.snapshot_root == record["candidate_path"]
            and candidate.snapshot_tree_oid == record["binding"]["tree_oid"]
            and hmac.compare_digest(
                candidate.snapshot_binding_digest or "",
                _snapshot_binding_digest(record["binding"]),
            )
        )

    def _invoke_bound_tool(self, state: _TaskState, context: SelfEvolutionWorkerContext,
                           tool: HostTool, arguments: Mapping[str, object]) -> Mapping[str, object]:
        if context.task_id != state.task_id or context.attempt_id != (state.identity.attempt_id if state.identity else None):
            raise PermissionError("worker identity mismatch")
        if not self._worker_binding_valid(state):
            raise PermissionError("candidate binding unavailable")
        if (self._static_ast_profile and tool.name == "write_candidate_file"
                and not _static_ast_write_path_allowed(
                    arguments.get("path"), self._config.attribution_paths,
                    self._config.verification_source_paths,
                )):
            return {"status": "rejected", "error_code": "candidate_write_not_allowlisted"}
        if tool.name == "run_registered_check":
            # Static AST is a distinct non-executing evidence type. Every normal
            # registered check retains the conservative process reconciliation path.
            is_static = self._static_ast_profile
            if not is_static:
                state.verification_attempted = True
            check_id = arguments.get("check_id")
            if type(check_id) is str:
                state.check_evidence_provenance.pop(check_id, None)
        result = tool.invoke(context, arguments)
        if not self._worker_binding_valid(state):
            raise RuntimeError("candidate binding changed during tool operation")
        if tool.name == "run_registered_check":
            self._record_check_evidence_provenance(state, arguments, result)
        self._record_tool_milestone(state, tool.name, arguments, result)
        return result

    def _run_static_ast_check(self, state: _TaskState, context: SelfEvolutionWorkerContext,
                              arguments: Mapping[str, object]) -> Mapping[str, object]:
        if not self._static_ast_profile or not self._worker_binding_valid(state):
            raise PermissionError("static verification profile unavailable")
        check_id = arguments.get("check_id")
        if type(check_id) is not str or check_id != "static_ast":
            raise PermissionError("unregistered static verification check")
        baseline = state.snapshot_review_baseline
        if baseline is None:
            raise RuntimeError("frozen candidate baseline unavailable")
        expected_digest = self._current_candidate_digest(state)
        if expected_digest is None:
            raise RuntimeError("full candidate digest unavailable")
        evidence = _static_ast_evidence(
            baseline, candidate_id=self._candidate_identity(state.workspace_record),
            allowed_paths=self._config.verification_source_paths,
            expected_candidate_digest=expected_digest,
            baseline_manifest=state.snapshot_tree_baseline,
        )
        return {**evidence, "check_id": check_id, "evidence_ref": uuid.uuid4().hex}

    def _record_check_evidence_provenance(self, state: _TaskState, arguments: Mapping[str, object],
                                          result: Mapping[str, object]) -> None:
        """Retain trusted runner facts that the worker projection intentionally omits."""
        check_id = arguments.get("check_id")
        if type(check_id) is not str or not check_id or state.identity is None:
            return
        if result.get("check_id") != check_id:
            return
        digest = self._current_candidate_digest(state)
        if (not isinstance(digest, str) or result.get("candidate_digest") != digest
                or result.get("status") not in {"passed", "failed", "unknown"}):
            return
        verification_type = result.get("verification_type", "execution")
        tree_state = result.get("process_tree_state")
        reconciliation = result.get("requires_reconciliation")
        scope = result.get("evidence_scope")
        exit_code = result.get("exit_code")
        if verification_type == "static_ast":
            baseline = state.snapshot_review_baseline
            if (not self._static_ast_profile or baseline is None
                    or result.get("baseline_digest") != baseline.baseline_digest
                    or result.get("before_candidate_digest") != digest
                    or result.get("after_candidate_digest") != digest
                    or tree_state != "not_applicable" or reconciliation is not False
                    or exit_code is not None or scope != "full_candidate_point_in_time"):
                return
        elif tree_state not in {"not_started", "stopped", "unknown"} or type(reconciliation) is not bool:
            return
        if type(exit_code) is not int and exit_code is not None:
            return
        if type(scope) is not str or len(scope) > 128:
            return
        state.check_evidence_provenance[check_id] = {
            "status": result["status"], "candidate_digest": digest,
            "exit_code": exit_code, "process_tree_state": tree_state,
            "requires_reconciliation": reconciliation, "evidence_scope": scope,
            "verification_type": verification_type,
            "baseline_digest": result.get("baseline_digest"),
            "before_candidate_digest": result.get("before_candidate_digest"),
            "after_candidate_digest": result.get("after_candidate_digest"),
        }

    def _record_tool_milestone(self, state: _TaskState, name: str, arguments: Mapping[str, object],
                               result: Mapping[str, object]) -> None:
        if self._store is None or state.identity is None:
            return
        ref = result.get("evidence_ref")
        if type(ref) is not str or not ref.strip() or len(ref) > 512:
            return
        if name == "observe_candidate" and result.get("status") in {"observed", "ok"}:
            fact = "observation_recorded"
        elif name == "write_candidate_file" and result.get("status") in {"written", "ok"}:
            fact = "edit_started"
        elif (name == "run_registered_check" and result.get("status") in {"passed", "failed", "unknown"}
              and result.get("check_id") == arguments.get("check_id")
              and result.get("candidate_digest") == self._current_candidate_digest(state)):
            fact = "check_started"
        else:
            return
        # The underlying tool reference may be a private URI or path. Progress
        # events carry only an opaque, non-resolving handle; never leak raw refs.
        self._append_fact_event(state, self._current_phase(state), (fact,), (uuid.uuid4().hex,))

    def _current_phase(self, state: _TaskState) -> str:
        if self._store is None or state.identity is None:
            raise StoreError("persistent host unavailable")
        current = self._store.read_task(state.identity.session_id, state.task_id)
        if current is None or current.identity != state.identity:
            raise StoreConflict("worker phase identity mismatch")
        return current.phase

    def _append_fact_event(self, state: _TaskState, phase: str, facts: tuple[str, ...],
                           evidence_refs: tuple[str, ...] = ()) -> None:
        """Atomically advance the snapshot, durable event, and sequence watermark."""
        if self._store is None or state.identity is None:
            raise StoreError("persistent host unavailable")
        with state.lock:
            current = self._store.read_task(state.identity.session_id, state.task_id)
            if current is None or current.identity != state.identity or current.phase != phase:
                raise StoreConflict("event snapshot identity or phase mismatch")
            changed = replace(current, snapshot_revision=current.snapshot_revision + 1)
            sequence = state.sequence + 1
            event = EvolutionEvent(state.identity, sequence, phase, "phase_changed", facts,
                                   changed.snapshot_revision, evidence_refs)
            try:
                self._store.commit_transition(state.identity, current.snapshot_revision, changed, event)
            except Exception:
                self._mark_unknown(state, "event_persistence_failed")
                raise
            state.sequence = sequence

    def _worker_binding_valid(self, state: _TaskState) -> bool:
        if state.cancel_event is None or state.cancel_event.is_set() or state.identity is None:
            return False
        snapshot = self._store.read_task(state.identity.session_id, state.task_id) if self._store else None
        if snapshot is None or snapshot.identity != state.identity or snapshot.phase in {"unknown", "cancelled", "failed", "merged", "cancelling"}:
            return False
        record, error = self._inspect_bound_record(state)
        return record is not None and error is None

    def _current_candidate_digest(self, state: _TaskState) -> str | None:
        if state.workspace_record.get("candidate_kind") == "materialized_snapshot":
            # A bounded Windows-handle sweep, not an atomic filesystem snapshot.
            # This digest is evidence of one observed tree, not a production gate.
            try:
                if not self._worker_binding_valid_without_phase(state):
                    return None
                baseline = state.snapshot_review_baseline
                record, failure = self._inspect_bound_record(state)
                if failure or record is None or baseline is None or not baseline.full_tree:
                    return None
                candidate_id = self._candidate_identity(record)
                snapshot_review._validate_baseline(baseline, candidate_id)
                if os.path.normcase(os.path.abspath(baseline.root)) != os.path.normcase(os.path.abspath(record["candidate_path"])):
                    return None
                observed = snapshot_review._capture(record["candidate_path"])
                if observed.root_identity != baseline.root_identity:
                    return None
                if not self._worker_binding_valid_without_phase(state):
                    return None
                final = snapshot_review._capture(record["candidate_path"])
                if (final.root_identity != baseline.root_identity or final.digest != observed.digest
                        or not self._worker_binding_valid_without_phase(state)):
                    return None
                return final.digest
            except Exception:
                return None
        # Attribution currently covers only host-configured paths, not every tracked,
        # untracked, ignored, or metadata-relevant candidate entry. It is not a
        # trustworthy whole-candidate digest and cannot authorize worker checks.
        if not self.full_candidate_coverage_available:
            return None
        try:
            if not self._worker_binding_valid_without_phase(state):
                return None
            if state.attribution_baseline is None:
                return None
            evidence = check_changes(state.attribution_baseline)
            return evidence.get("summary_sha256") if evidence.get("safe_to_consider") else None
        except Exception:
            return None

    def _worker_binding_valid_without_phase(self, state: _TaskState) -> bool:
        if state.identity is None or state.cancel_event is None or state.cancel_event.is_set():
            return False
        snapshot = self._store.read_task(state.identity.session_id, state.task_id) if self._store else None
        if snapshot is None or snapshot.identity != state.identity or snapshot.phase in {"unknown", "cancelled", "failed", "merged", "cancelling"}:
            return False
        record, error = self._inspect_bound_record(state)
        return record is not None and error is None

    def _persist_worker_phase(self, state: _TaskState, phase: str) -> None:
        # The worker's awaiting_review event precedes Host verification-evidence
        # settlement. Never publish that phase until the Host atomically commits
        # the final digest-bound evidence and releases/quarantines the lease.
        if phase == "awaiting_review":
            return
        if self._store is None or state.identity is None:
            raise StoreError("persistent host unavailable")
        if phase not in {"observing", "editing", "checking"}:
            raise StoreConflict("worker phase is not in the host allowlist")
        with state.lock:
            current = self._store.read_task(state.identity.session_id, state.task_id)
            if current is None or current.identity != state.identity:
                raise StoreConflict("worker phase identity mismatch")
            if current.phase == phase:
                return
            changed = replace(current, phase=phase, snapshot_revision=current.snapshot_revision + 1,
                              allowed_next_actions=("cancel", "get_snapshot"))
            sequence = state.sequence + 1
            event = EvolutionEvent(state.identity, sequence, phase, "phase_changed", (), changed.snapshot_revision)
            try:
                self._store.commit_transition(state.identity, current.snapshot_revision, changed, event)
            except Exception:
                self._mark_unknown(state, "event_persistence_failed")
                raise
            state.sequence = sequence

    def _settle_worker_result(self, state: _TaskState, result: SelfEvolutionWorkerResult,
                              *, stopped_confirmed: bool) -> None:
        assert self._store is not None and state.identity is not None and state.owner_id is not None
        with state.lock:
            current = self._store.read_task(state.identity.session_id, state.task_id)
            if current is None or current.identity != state.identity:
                self._mark_unknown(state, "worker_result_identity_mismatch")
                return
            digest = self._current_candidate_digest(state)
            if digest is None:
                self._mark_unknown(state, "candidate_digest_unavailable_at_settlement")
                return
            evidence: list[VerificationEvidence] = []
            # A Job Object's active count reaching zero does not cover processes
            # created by brokers such as WMI. Neither its scope label nor a worker
            # stop callback proves the entire verification execution has stopped.
            # Until there is a trusted containment/reconciliation mechanism with
            # that stronger guarantee, every attempted check retains ownership.
            static_profile = self._static_ast_profile
            execution_evidence_attempted = state.verification_attempted or any(
                value.get("verification_type") != "static_ast"
                for value in state.check_evidence_provenance.values()
            ) or any(item.get("verification_type", "execution") != "static_ast"
                     for item in result.check_evidence)
            requires_reconciliation = bool(execution_evidence_attempted)
            for item in result.check_evidence:
                check_id = item.get("check_id")
                if type(check_id) is not str or not check_id or len(check_id) > 256:
                    check_id = "registered_check"
                provenance = state.check_evidence_provenance.get(check_id)
                if not provenance or provenance.get("candidate_digest") != digest:
                    provenance = None
                if (static_profile and provenance is not None
                        and provenance.get("verification_type") == "static_ast"
                        and provenance.get("before_candidate_digest") == digest
                        and provenance.get("after_candidate_digest") == digest
                        and state.snapshot_review_baseline is not None
                        and provenance.get("baseline_digest") == state.snapshot_review_baseline.baseline_digest
                        and provenance.get("process_tree_state") == "not_applicable"
                        and provenance.get("requires_reconciliation") is False):
                    evidence.append(VerificationEvidence(
                        check_id=check_id, candidate_digest=digest,
                        status=provenance["status"], exit_code=None,
                        process_tree_state="not_applicable", evidence_ref=uuid.uuid4().hex,
                        verification_type="static_ast",
                        baseline_digest=provenance["baseline_digest"],
                        before_candidate_digest=digest, after_candidate_digest=digest,
                    ))
                    continue
                # Worker-provided status/process fields are not authoritative. Only
                # host-held provenance may supply facts; execution still requires
                # reconciliation and cannot be upgraded by static evidence.
                exit_code = provenance.get("exit_code") if provenance else None
                if type(exit_code) is not int:
                    exit_code = None
                evidence.append(VerificationEvidence(
                    check_id=check_id, candidate_digest=digest,
                    status="unknown", exit_code=exit_code, process_tree_state="unknown",
                    evidence_ref=uuid.uuid4().hex if provenance else "worker_check_unconfirmed",
                ))
            if requires_reconciliation:
                phase, blocker = "unknown", "verification_requires_reconciliation"
            elif result.status == "finished" and (not evidence or any(item.status != "passed" for item in evidence)):
                phase, blocker = "blocked", "verification_evidence_unbound"
            elif result.status == "finished":
                phase, blocker = "awaiting_review", None
            elif result.status == "cancelled":
                phase, blocker = "cancelled", None
            elif result.status == "failed":
                phase, blocker = "failed", result.error_code or "worker_failed"
            else:
                phase, blocker = "blocked", result.error_code or "worker_blocked"
            # Snapshot, terminal fact, event watermark, and writer release form one
            # transaction. If event persistence fails, the writer remains held and
            # the caller can atomically quarantine the attempt as unknown.
            latest = current
            public_summary = (
                "\u5019\u9009\u6539\u9020\u5df2\u5b8c\u6210\u7ed1\u5b9a\u7248\u672c\u7684\u56fa\u5b9a\u68c0\u67e5\uff0c\u7b49\u5f85\u4f60\u5ba1\u6838\u3002"
                if phase == "awaiting_review" and evidence
                and all(item.status == "passed" for item in evidence) else None
            )
            changed = replace(latest, phase=phase, snapshot_revision=latest.snapshot_revision + 1,
                              candidate_digest=digest, evidence=tuple(evidence), change_summary=public_summary,
                              blocker_code=blocker, error_code=blocker,
                              allowed_next_actions=("revise", "get_snapshot") if phase in {"awaiting_review", "blocked"} else ("get_snapshot",))
            if phase == "awaiting_review":
                facts, kind, refs = ("awaiting_review",), "phase_changed", ()
            elif phase == "blocked":
                facts, kind, refs = ("blocked",), "task_blocked", ()
            elif phase in {"cancelled", "failed"}:
                facts, kind, refs = (phase,), "task_terminal", ()
            elif phase == "unknown":
                facts, kind, refs = ("unknown",), "task_terminal", ()
            else:
                facts, kind, refs = (), "phase_changed", ()
            sequence = state.sequence + 1
            event = EvolutionEvent(state.identity, sequence, phase, kind, facts,
                                   changed.snapshot_revision, refs)
            try:
                self._store.commit_transition(state.identity, latest.snapshot_revision, changed, event,
                                              release_owner_id=None if requires_reconciliation else state.owner_id,
                                              stop_confirmed=stopped_confirmed if not requires_reconciliation else False,
                                              summary_candidate_digest=digest if public_summary is not None else None,
                                              quarantine_owner_id=state.owner_id if requires_reconciliation else None)
            except Exception:
                self._mark_unknown(state, "settlement_commit_failed")
                return
            state.writer_held = requires_reconciliation
            state.sequence = sequence

    def _mark_unknown(self, state: _TaskState, code: str) -> None:
        if state.guidance_inbox is not None:
            state.guidance_inbox.close(code)
        if self._store is None or state.identity is None:
            return
        try:
            with state.lock:
                current = self._store.read_task(state.identity.session_id, state.task_id)
                if current is None or current.identity != state.identity or current.phase == "unknown":
                    return
                if state.owner_id and state.writer_held:
                    # The persistent store owns orphan transitions and their durable
                    # event. Do not emit a second host-side unknown event here.
                    self._store.mark_orphaned_unknown(state.owner_id)
                    return
                changed = self._store.mark_task_unknown(state.task_id, code[:256])
                state.sequence = max(state.sequence, changed.snapshot_revision - 1)
        except Exception:
            # A failed unknown write is still fail-closed: never release the lease.
            return

    def create(self, *, expected_context_identity: tuple[str, str, int, str] | None = None,
               require_self_evolution_intent: bool = False) -> dict:
        """Create a candidate, optionally bound to an exact identity and explicit intent."""
        context, error = self._live_context(
            expected=expected_context_identity,
            require_self_evolution_intent=require_self_evolution_intent,
        )
        if error:
            return _denied(error)
        assert context is not None
        if getattr(self, "_startup_reconciliation_error", None) is not None:
            return _denied("startup_state_unresolved")
        snapshot_creation = (
            self.snapshot_candidate_creation_available
            and callable(getattr(self._workspace, "create_snapshot_candidate", None))
        )
        if self._static_ast_profile and not snapshot_creation:
            return _failed("experimental_windows_snapshot_unavailable")
        if snapshot_creation and self._snapshot_materializer is None:
            return _failed("snapshot_materializer_unavailable")
        try:
            if snapshot_creation:
                raw = self._workspace.create_snapshot_candidate(self._snapshot_materializer)
            else:
                # Compatibility for existing trusted Host workspace adapters;
                # the production SelfEvolutionWorkspace always prefers snapshots.
                raw = self._workspace.create()
        except WorkspaceError as exc:
            result = _failed(exc.code, task_id=exc.candidate_id if _is_canonical_uuid(exc.candidate_id) else None)
            if exc.publication_status in {"published", "unknown"}:
                result["publication_status"] = exc.publication_status
            if exc.publication_status == "unknown":
                result["status"] = "unknown"
            return result
        except Exception:
            return _failed("candidate_creation_unknown")
        if raw.get("status") != "created":
            failure = raw.get("failure") if isinstance(raw.get("failure"), dict) else {}
            return _failed(str(failure.get("code") or "candidate_creation_failed"))
        task_id = raw.get("task_id")
        if type(task_id) is not str or not task_id:
            return _failed("host_task_identity_missing")
        try:
            if raw.get("candidate_kind") == "materialized_snapshot":
                record = self._inspect_snapshot_record(task_id)
                candidate_identity = self._candidate_identity(record)
                # Freeze the complete bounded candidate tree before any worker
                # can be dispatched. This scan is non-atomic and grants no
                # Host full-coverage or merge authority.
                review_baseline = snapshot_review.freeze_baseline(
                    candidate_root=record["candidate_path"],
                    candidate_id=candidate_identity,
                    paths=None,
                )
                if review_baseline.root_identity != tuple(record["binding"]["directory_identity"]):
                    raise WorkspaceError("Frozen review root differs from the authenticated candidate.",
                                         code="snapshot_root_identity_mismatch", stage="register")
                if self._inspect_snapshot_record(task_id) != record:
                    raise WorkspaceError("Snapshot binding changed while freezing the baseline.",
                                         code="snapshot_binding_changed_during_registration", stage="register")
                tree_baseline = snapshot_review._capture(record["candidate_path"])
                if (tree_baseline.digest != review_baseline.baseline_digest
                        or tree_baseline.root_identity != review_baseline.root_identity
                        or not tree_baseline.full_candidate_coverage or tree_baseline.excluded_metadata):
                    raise WorkspaceError("Full candidate manifest changed during baseline capture.",
                                         code="snapshot_manifest_changed_during_registration", stage="register")
            else:
                record = self._workspace.inspect(task_id)
                review_baseline = None
                tree_baseline = None
            baseline = capture_baseline(
                self._config.stable_repository,
                raw["candidate_path"],
                self._config.attribution_paths,
            )
            if record != raw:
                raise WorkspaceError(
                    "Candidate record changed during Host registration.",
                    code="candidate_record_changed", stage="register",
                )
        except (snapshot_review.ReviewError, WorkspaceError, KeyError, AttributionError, OSError, ValueError) as exc:
            if isinstance(exc, snapshot_review.ReviewError):
                code = f"snapshot_review_baseline_{exc.code}"
            elif isinstance(exc, WorkspaceError):
                code = exc.code
            else:
                code = "candidate_registration_failed"
            failure = _failed(code, task_id=task_id)
            if raw.get("candidate_kind") == "materialized_snapshot":
                failure["publication_status"] = "published"
            return failure
        state = _TaskState(
            task_id=task_id,
            context_identity=context.identity(),
            workspace_record=record,
            attribution_baseline=baseline,
            snapshot_review_baseline=review_baseline,
            snapshot_tree_baseline=tree_baseline,
            created_evidence=(
                {
                    "candidate_kind": "materialized_snapshot",
                    "snapshot_scope": record["snapshot_scope"],
                    "stable_head": record["stable_head"],
                    "candidate_head": None,
                    "baseline_manifest_digest": record["baseline_manifest_digest"],
                    "tree_oid": record["binding"]["tree_oid"],
                    "os_sandbox": False,
                    "merge_authorized": False,
                } if record.get("candidate_kind") == "materialized_snapshot" else {
                    "stable_head": record["stable_head"],
                    "candidate_head": record["candidate_head"],
                    "object_store": record["object_store"],
                    "history_depth": record["history_depth"],
                    "os_sandbox": False,
                }
            ),
        )
        with self._tasks_lock:
            if task_id in self._tasks:
                return _failed("host_task_identity_collision")
            self._tasks[task_id] = state
        current, error = self._live_context(
            expected=state.context_identity,
            require_self_evolution_intent=require_self_evolution_intent,
        )
        if error:
            return _unknown("candidate_created_context_ended", task_id=task_id,
                            candidate=state.created_evidence)
        assert current is not None
        return {
            "status": "created",
            "task_id": task_id,
            "binding": _binding_dict(state.context_identity),
            "candidate": dict(state.created_evidence),
            "worker_bound": False,
            "workflow_status": "incomplete_worker_unbound",
            "os_sandbox": False,
            "merge_authorized": False,
            "merge_status": "unavailable_native_merge_host_not_connected",
        }

    def inspect(self, task_id: str) -> dict:
        state, context, error = self._authorize_task(task_id)
        if error:
            return _denied(error)
        assert state is not None and context is not None
        with state.lock:
            record, failure = self._inspect_bound_record(state)
            if failure:
                return _failed(failure, task_id=state.task_id)
            _, error = self._live_context(expected=state.context_identity)
            if error:
                return _denied(error)
            return {
                "status": "inspected",
                "task_id": state.task_id,
                "binding": _binding_dict(state.context_identity),
                "candidate": dict(state.created_evidence),
                "worker_bound": False,
                "os_sandbox": False,
                "merge_authorized": False,
            }

    def review_candidate(self, task_id: str) -> dict:
        """Review changed-file metadata only; never disclose source contents."""
        state, _context, error = self._authorize_task(task_id)
        if error:
            return _denied(error)
        assert state is not None
        with state.lock:
            record, failure = self._inspect_bound_record(state)
            baseline = state.snapshot_review_baseline
            if failure:
                return _failed(failure, task_id=state.task_id)
            if (record is None or baseline is None
                    or record.get("candidate_kind") != "materialized_snapshot"):
                return _failed("snapshot_review_baseline_unavailable", task_id=state.task_id)
            candidate_identity = self._candidate_identity(record)
            if (baseline.candidate_id != candidate_identity
                    or os.path.normcase(os.path.abspath(baseline.root))
                    != os.path.normcase(os.path.abspath(record["candidate_path"]))):
                return _failed("snapshot_review_binding_mismatch", task_id=state.task_id)
            try:
                result = snapshot_review.review_candidate(
                    baseline, candidate_id=candidate_identity, disclose_paths=(),
                )
                result["full_tree_baseline_mode"] = baseline.full_tree
                result["full_candidate_coverage"] = False
                result["snapshot_coverage_complete"] = False
                result["host_coverage_claim_permitted"] = False
                result["review_is_atomic"] = False
            except snapshot_review.ReviewError as exc:
                return _failed(exc.code, task_id=state.task_id)
            except Exception:
                return _failed("snapshot_review_failed", task_id=state.task_id)
            _, error = self._live_context(expected=state.context_identity)
            if error:
                return _denied(error)
            return {
                "status": "reviewed",
                "task_id": state.task_id,
                "review": result,
                "worker_bound": False,
                "os_sandbox": False,
                "merge_authorized": False,
                "host_coverage_claim_permitted": False,
            }

    def review_candidate_content(self, task_id: str) -> dict:
        """Disclose bounded diffs only after trusted, digest-bound Host approval.

        No paths or approval flags are accepted from the caller. A configured
        Host callback sees metadata for this exact authenticated task/attempt
        and returns the approved changed paths bound to ``request_sha256``.
        The existing create-time baseline is reused; this method never refreezes
        or recaptures its reference bytes. Candidate content changes between the
        metadata prompt and the approved read cause the entire disclosure to fail.
        """
        state, _context, error = self._authorize_task(task_id)
        if error:
            return _denied(error)
        assert state is not None

        def still_authorized() -> bool:
            _context, live_error = self._live_context(expected=state.context_identity)
            return live_error is None

        return self._review_candidate_content_bound(state, still_authorized)

    def review_candidate_content_for_session(self, task_id: str, attempt_id: str) -> dict:
        """Review an existing attempt under a live HostReadContext session lease.

        This does not issue a disclosure grant: the independently injected trusted
        callback remains mandatory. Caller identifiers are selectors, not authority.
        The baseline is in-process only; restart without state returns unavailable.
        """
        return self._review_candidate_content_for_session_with_disclosure(task_id, attempt_id, None)

    def _review_candidate_content_for_session_with_disclosure(
        self, task_id: str, attempt_id: str, disclosure_fn: Callable | None,
    ) -> dict:
        # Backend-only: a transport may supply a trusted, request-scoped callback.
        # This parameter must never be populated from client/model input.
        try:
            context = self._context_provider()
        except Exception:
            return _denied("run_context_unavailable")
        if (type(context) is not HostReadContext or not context.active
                or type(task_id) is not str or not task_id
                or type(attempt_id) is not str or not attempt_id):
            return _denied("review_read_context_required")
        snapshot = self.get_snapshot(task_id, session_id=context.conversation_id)
        with self._tasks_lock:
            state = self._tasks.get(task_id)
        if (snapshot is None or state is None or state.identity is None
                or snapshot.identity != state.identity
                or snapshot.identity.attempt_id != attempt_id
                or snapshot.identity.session_id != context.conversation_id):
            return _denied("review_attempt_unavailable")

        def still_authorized() -> bool:
            if not self._read_context_still_matches(context):
                return False
            with self._tasks_lock:
                if self._tasks.get(task_id) is not state:
                    return False
            current = self.get_snapshot(task_id, session_id=context.conversation_id)
            return current == snapshot and state.identity == snapshot.identity

        if not still_authorized():
            return _denied("review_read_context_expired")
        return self._review_candidate_content_bound(state, still_authorized, disclosure_fn)

    def _review_candidate_content_bound(self, state: _TaskState,
                                        still_authorized: Callable[[], bool],
                                        disclosure_fn: Callable | None = None) -> dict:
        disclosure_fn = disclosure_fn if disclosure_fn is not None else self._trusted_review_disclosure_fn
        if not callable(disclosure_fn):
            return _failed("content_disclosure_callback_unavailable", task_id=state.task_id)
        with state.lock:
            # Denied or stale new reviews cannot leave an earlier selection usable.
            state.reviewed_content_binding = None
            record, failure = self._inspect_bound_record(state)
            baseline = state.snapshot_review_baseline
            if failure:
                return _failed(failure, task_id=state.task_id)
            if (record is None or baseline is None
                    or record.get("candidate_kind") != "materialized_snapshot"):
                return _failed("snapshot_review_baseline_unavailable", task_id=state.task_id)
            candidate_identity = self._candidate_identity(record)
            if (baseline.candidate_id != candidate_identity or not baseline.full_tree
                    or os.path.normcase(os.path.abspath(baseline.root))
                    != os.path.normcase(os.path.abspath(record["candidate_path"]))):
                return _failed("snapshot_review_binding_mismatch", task_id=state.task_id)
            attempt_id, failure = self._review_attempt_id(state)
            if failure:
                return _failed(failure, task_id=state.task_id)
            try:
                metadata = snapshot_review.review_candidate(
                    baseline, candidate_id=candidate_identity, disclose_paths=(),
                )
            except snapshot_review.ReviewError as exc:
                return _failed(exc.code, task_id=state.task_id)
            except Exception:
                return _failed("snapshot_review_failed", task_id=state.task_id)
            if len(metadata["files"]) > 256:
                return _failed("content_disclosure_scope_exceeded", task_id=state.task_id)
            changed_files = tuple({
                "path": row["path"], "change": row["change"],
                "content_changed": row["content_changed"],
                "before_sha256": row["before_sha256"], "after_sha256": row["after_sha256"],
                "before_file_identity": row["before_file_identity"],
                "after_file_identity": row["after_file_identity"],
                "before_mode": row["before_mode"], "after_mode": row["after_mode"],
                "before_content_status": row["before_content_status"],
                "after_content_status": row["after_content_status"],
            } for row in metadata["files"])
            request_body = {
                "schema": "self_evolution_content_disclosure_request_v1",
                "session_id": state.context_identity[0], "task_id": state.task_id,
                "attempt_id": attempt_id, "candidate_id": candidate_identity,
                "baseline_digest": metadata["baseline_digest"],
                "candidate_digest": metadata["candidate_digest"],
                "approved_scope": "changed_files_only", "changed_files": changed_files,
            }
            request_sha256 = hashlib.sha256(json.dumps(
                request_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("ascii")).hexdigest()
            callback_request = MappingProxyType({
                **request_body,
                "changed_files": tuple(MappingProxyType(row) for row in changed_files),
                "request_sha256": request_sha256,
            })
            if not still_authorized():
                return _denied("review_read_context_expired")
            try:
                decision = disclosure_fn(callback_request)
            except Exception:
                return _failed("content_disclosure_decision_failed", task_id=state.task_id)
            if type(decision) is not ReviewDisclosureDecision:
                return _failed("content_disclosure_decision_invalid", task_id=state.task_id)
            if not decision.approved:
                return _failed("content_disclosure_denied", task_id=state.task_id)
            if not hmac.compare_digest(decision.request_sha256, request_sha256):
                return _failed("content_disclosure_binding_mismatch", task_id=state.task_id)
            changed_paths = {row["path"] for row in changed_files}
            approved_paths = decision.approved_paths
            if (not approved_paths or len(approved_paths) > 256
                    or len(set(approved_paths)) != len(approved_paths)
                    or not set(approved_paths).issubset(changed_paths)):
                return _failed("content_disclosure_paths_invalid", task_id=state.task_id)
            if not still_authorized():
                return _denied("review_read_context_expired")
            record, failure = self._inspect_bound_record(state)
            if failure or record is None:
                return _failed(failure or "candidate_inspection_failed", task_id=state.task_id)
            attempt_id_after, failure = self._review_attempt_id(state)
            if failure or attempt_id_after != attempt_id:
                return _failed(failure or "review_attempt_changed", task_id=state.task_id)
            try:
                result = snapshot_review.review_candidate(
                    baseline, candidate_id=candidate_identity, disclose_paths=approved_paths,
                )
            except snapshot_review.ReviewError as exc:
                return _failed(exc.code, task_id=state.task_id)
            except Exception:
                return _failed("snapshot_review_failed", task_id=state.task_id)
            if (result.get("baseline_digest") != metadata.get("baseline_digest")
                    or result.get("candidate_digest") != metadata.get("candidate_digest")):
                return _failed("candidate_changed_after_disclosure", task_id=state.task_id)
            result["full_tree_baseline_mode"] = baseline.full_tree
            result["full_candidate_coverage"] = False
            result["snapshot_coverage_complete"] = False
            result["host_coverage_claim_permitted"] = False
            result["review_is_atomic"] = False
            if not still_authorized():
                return _denied("review_read_context_expired")
            # Only non-truncated, actually disclosed changed files can be offered
            # as selected apply candidates. This is not native merge approval.
            disclosed = tuple(sorted(row["path"] for row in result["files"]
                                     if row["path"] in approved_paths and "diff" in row))
            state.reviewed_content_binding = (
                attempt_id, result["candidate_digest"], disclosed, request_sha256
            ) if attempt_id is not None and disclosed and not result["truncated"] else None
            result["disclosure_binding"] = {
                "session_id": state.context_identity[0], "task_id": state.task_id,
                "attempt_id": attempt_id, "candidate_id": candidate_identity,
                "baseline_digest": metadata["baseline_digest"],
                "candidate_digest": metadata["candidate_digest"],
                "request_sha256": request_sha256, "approved_paths": approved_paths,
            }
            return {
                "status": "reviewed", "task_id": state.task_id, "review": result,
                "worker_bound": False, "os_sandbox": False, "merge_authorized": False,
                "host_coverage_claim_permitted": False,
            }

    def apply_reviewed_candidate_for_session(
        self,
        task_id: str,
        attempt_id: str,
        *,
        proposed_paths: tuple[str, ...] | list[str] | None = None,
    ) -> dict:
        """Apply once after native approval; paths are proposals, never authority.

        In the static_ast lane, proposed paths must be an exact subset of the
        content already disclosed for this session/attempt. Host rebinds them to
        the immutable candidate digest and obtains a separate native claim.
        """
        if not callable(self._trusted_native_apply_claim_fn):
            return _failed("native_apply_bridge_unavailable", task_id=task_id)
        if self._static_ast_profile:
            if not self.experimental_apply_target_ready:
                return _failed("experimental_disposable_target_unavailable", task_id=task_id)
            if proposed_paths is None:
                return _failed("apply_path_proposal_required", task_id=task_id)
        elif not callable(self._trusted_selected_review_paths_fn):
            return _failed("selected_path_binding_unavailable", task_id=task_id)
        elif proposed_paths is not None:
            return _failed("apply_path_proposal_not_available", task_id=task_id)
        try:
            context = self._context_provider()
        except Exception:
            return _denied("run_context_unavailable")
        if (type(context) is not HostReadContext or not context.active
                or type(task_id) is not str or not task_id
                or type(attempt_id) is not str or not attempt_id):
            return _denied("review_read_context_required")
        snapshot = self.get_snapshot(task_id, session_id=context.conversation_id)
        with self._tasks_lock:
            state = self._tasks.get(task_id)
        if (snapshot is None or state is None or state.identity is None
                or snapshot.identity != state.identity or snapshot.identity.attempt_id != attempt_id
                or snapshot.phase != "awaiting_review"):
            return _denied("review_attempt_unavailable")
        with state.lock:
            binding = state.reviewed_content_binding
            # Consume even on denial or failure. Another operation requires a new review.
            state.reviewed_content_binding = None
            if not binding or binding[0] != attempt_id:
                return _failed("reviewed_content_binding_unavailable", task_id=task_id)
            def guard() -> bool:
                if self._static_ast_profile and not self.experimental_apply_target_ready:
                    return False
                if not self._read_context_still_matches(context):
                    return False
                with self._tasks_lock:
                    if self._tasks.get(task_id) is not state:
                        return False
                if self.get_snapshot(task_id, session_id=context.conversation_id) != snapshot:
                    return False
                current_attempt, failure = self._review_attempt_id(state)
                return failure is None and current_attempt == attempt_id
            if not guard():
                return _denied("review_read_context_expired")
            record, failure = self._inspect_bound_record(state)
            baseline = state.snapshot_review_baseline
            if failure or record is None or baseline is None:
                return _failed(failure or "snapshot_review_baseline_unavailable", task_id=task_id)
            if self._static_ast_profile:
                try:
                    selected = _validate_apply_path_proposal(proposed_paths, binding[2])
                except ValueError:
                    return _failed("reviewed_selection_invalid", task_id=task_id)
            else:
                selection_request = MappingProxyType({
                    "session_id": context.conversation_id, "task_id": task_id, "attempt_id": attempt_id,
                    "candidate_digest": binding[1], "review_request_sha256": binding[3],
                    "reviewed_paths": binding[2],
                })
                try:
                    selected = self._trusted_selected_review_paths_fn(selection_request)
                    if (type(selected) is not tuple or not selected or any(type(path) is not str for path in selected)
                            or len(set(selected)) != len(selected) or not set(selected).issubset(binding[2])):
                        return _failed("reviewed_selection_invalid", task_id=task_id)
                except Exception:
                    return _failed("reviewed_selection_invalid", task_id=task_id)
            try:
                from self_evolution_merge_integration import prepare_host_selected_apply, HostApplyError
                prepared = prepare_host_selected_apply(
                    repository_root=self._config.stable_repository, baseline=baseline,
                    session_id=context.conversation_id, task_id=task_id, attempt_id=attempt_id,
                    candidate_id=self._candidate_identity(record), selected_paths=tuple(sorted(selected)),
                    trusted_host_binding_is_current=guard,
                )
                preview = prepared.preview()
                if preview.get("candidate_digest") != binding[1]:
                    return _failed("candidate_changed_after_review", task_id=task_id)
                result = prepared.apply(trusted_native_claim=self._trusted_native_apply_claim_fn)
                return {"status": result.status, "task_id": task_id,
                        "error_code": result.error_code, "written_paths": result.written_paths,
                        "pending_paths": result.pending_paths, "manual_review_paths": result.manual_review_paths,
                        "recovery_paths": result.recovery_paths, "global_atomic": False,
                        "git_merge_executed": False, "os_sandbox": False}
            except Exception as exc:
                from self_evolution_merge_integration import HostApplyError
                code = exc.code if isinstance(exc, HostApplyError) else "selected_apply_failed"
                return _failed(code, task_id=task_id)

    def _review_attempt_id(self, state: _TaskState) -> tuple[str | None, str | None]:
        """Return the authenticated attempt, rejecting active or unresolved writers."""
        identity = state.identity
        if state.writer_held:
            return None, "candidate_writer_not_stopped"
        if state.worker_thread is not None and state.worker_thread.is_alive():
            return None, "candidate_writer_not_stopped"
        if identity is None:
            return None, None
        expected_session, expected_run, expected_epoch, _request_id = state.context_identity
        if (type(identity) is not EvolutionIdentity or identity.session_id != expected_session
                or identity.task_id != state.task_id or identity.run_id != expected_run
                or identity.turn_epoch != expected_epoch):
            return None, "review_attempt_binding_mismatch"
        if self._store is None:
            return None, "review_attempt_state_unavailable"
        try:
            snapshot = self._store.read_task(identity.session_id, identity.task_id)
        except Exception:
            return None, "review_attempt_state_unavailable"
        if (snapshot is None or snapshot.identity != identity
                or snapshot.phase not in {"awaiting_review", "blocked", "failed", "cancelled"}):
            return None, "review_attempt_not_reviewable"
        return identity.attempt_id, None
    def attribute(self, task_id: str) -> dict:
        """Return read-only, process-sealed attribution evidence; never authorization."""
        state, _context, error = self._authorize_task(task_id)
        if error:
            return _denied(error)
        assert state is not None
        with state.lock:
            _record, failure = self._inspect_bound_record(state)
            if failure:
                return _failed(failure, task_id=state.task_id)
            try:
                if state.attribution_baseline is None:
                    return _failed("attribution_baseline_unavailable", task_id=state.task_id)
                evidence = _scope_attribution_evidence(check_changes(state.attribution_baseline))
            except (AttributionError, OSError, ValueError):
                return _failed("attribution_evidence_unavailable", task_id=state.task_id)
            _context, error = self._live_context(expected=state.context_identity)
            if error:
                return _denied(error)
            return {
                "status": "observed" if evidence.get("safe_to_consider") else "blocked",
                "task_id": state.task_id,
                "evidence_scope": "declared_paths_point_in_time_only",
                "full_candidate_coverage": False,
                "worker_authorization_eligible": False,
                "race_safe": False,
                "evidence": evidence,
                "worker_bound": False,
                "os_sandbox": False,
                "merge_authorized": False,
            }

    def verify(self, task_id: str) -> dict:
        """Run every fixed trusted check and report actual runner evidence."""
        state, context, error = self._authorize_task(task_id)
        if error:
            return _denied(error)
        assert state is not None and context is not None
        with state.lock:
            _record, failure = self._inspect_bound_record(state)
            if failure:
                return _failed(failure, task_id=state.task_id, verification_passed=False)
            check_results: list[VerificationResult] = []
            for command_id in self._config.verification_commands:
                _current, error = self._live_context(expected=state.context_identity)
                if error:
                    return _unknown(error, task_id=state.task_id,
                                    verification_passed=False, evidence={"checks": [_verification_dict(item) for item in check_results]})
                result = self._verification.run(
                    task_id=state.task_id,
                    command_id=command_id,
                    timeout_seconds=float(self._config.verification_timeout_seconds),
                    cancel_event=context.cancel_event,
                )
                check_results.append(result)
                _current, error = self._live_context(expected=state.context_identity)
                if error:
                    return _unknown(error, task_id=state.task_id,
                                    verification_passed=False, evidence={"checks": [_verification_dict(item) for item in check_results]})
            _record, failure = self._inspect_bound_record(state)
            if failure:
                return _unknown("candidate_changed_during_verification", task_id=state.task_id,
                                verification_passed=False,
                                evidence={"checks": [_verification_dict(item) for item in check_results]})
            try:
                attribution = check_changes(state.attribution_baseline) if state.attribution_baseline else None
            except (AttributionError, OSError, ValueError):
                attribution = None
            _current, error = self._live_context(expected=state.context_identity)
            if error:
                return _unknown(error, task_id=state.task_id, verification_passed=False,
                                evidence={"checks": [_verification_dict(item) for item in check_results],
                                          "attribution": attribution})
            state.verification_evidence = tuple(_verification_dict(item) for item in check_results)
            declared_checks_passed = bool(check_results) and all(item.outcome == "passed" for item in check_results)
            unknown = any(item.outcome == "unknown" for item in check_results)
            passed = declared_checks_passed and self.full_candidate_coverage_available
            status = ("verification_unknown" if unknown else
                      "verification_incomplete_coverage" if declared_checks_passed and not passed else
                      "verification_passed" if passed else "verification_failed")
            return {
                "status": status,
                "workflow_status": "incomplete_worker_unbound",
                "task_id": state.task_id,
                "verification_passed": passed,
                "evidence": {
                    "checks": list(state.verification_evidence),
                    "attribution": attribution,
                    "scope": "declared_paths_point_in_time_only",
                    "full_candidate_coverage": False,
                    "promotion_eligible": False,
                },
                "worker_bound": False,
                "os_sandbox": False,
                "merge_authorized": False,
                "merge_status": "unavailable_native_merge_host_not_connected",
            }

    def _authorize_task(self, task_id: str) -> tuple[_TaskState | None, HostRunContext | None, str | None]:
        context, error = self._live_context()
        if error:
            return None, None, error
        assert context is not None
        if type(task_id) is not str or not task_id:
            return None, None, "task_not_available"
        with self._tasks_lock:
            state = self._tasks.get(task_id)
        if state is None:
            return None, None, "task_not_available"
        if state.context_identity != context.identity():
            return None, None, "execution_binding_mismatch"
        return state, context, None

    def _live_context(
        self, *, expected: tuple[str, str, int, str] | None = None,
        require_self_evolution_intent: bool = False,
    ) -> tuple[HostRunContext | None, str | None]:
        try:
            context = self._context_provider()
        except Exception:
            return None, "run_context_unavailable"
        if type(context) is not HostRunContext or not context.active:
            return None, "run_not_live"
        if (not _valid_identity_part(context.conversation_id)
                or not _valid_identity_part(context.run_id)
                or isinstance(context.turn_epoch, bool) or not isinstance(context.turn_epoch, int)
                or context.turn_epoch < 0 or not callable(getattr(context.cancel_event, "is_set", None))
                or not _is_canonical_uuid(context.request_id)
                or context.request_origin != "human"):
            return None, "run_context_invalid"
        if require_self_evolution_intent and context.self_evolution_intent is not True:
            return None, "explicit_self_evolution_intent_required"
        if context.mode != "agent" or context.work_intent != "execute":
            return None, "execution_mode_denied"
        if context.cancel_event.is_set():
            return None, "cancelled"
        if expected is not None and context.identity() != expected:
            return None, "execution_binding_mismatch"
        return context, None

    def _inspect_snapshot_record(self, task_id: str) -> dict:
        """Revalidate an authenticated plain snapshot without Git-worktree claims."""
        if self._snapshot_materializer is None:
            raise WorkspaceError("Snapshot materializer is unavailable.", code="materializer_unavailable", stage="inspect")
        if type(task_id) is not str or not _is_canonical_uuid(task_id):
            raise WorkspaceError("Snapshot identifier is invalid.", code="identity_mismatch", stage="inspect")
        candidate = Path(self._config.trust_root) / task_id
        # The persisted binding authenticates the creation-time parent as well
        # as the candidate. Reuse the workspace's Host-captured root identity;
        # do not silently bless a replacement directory, including on restart.
        parent_identity = self._workspace._trust_root_identity
        if parent_identity is None:
            raise WorkspaceError("Snapshot trust root identity is unavailable.",
                                 code="snapshot_identity_unavailable", stage="inspect")
        def read_binding() -> dict:
            try:
                return self._snapshot_materializer.read_binding(
                    candidate, expected_candidate_id=task_id, integrity_key=self._key,
                    expected_parent_identity=parent_identity,
                )
            except Exception as exc:
                code = getattr(exc, "code", None) or "snapshot_identity_unavailable"
                raise WorkspaceError("Authenticated snapshot identity could not be inspected.",
                                     code=str(code), stage="inspect") from exc

        binding = read_binding()
        directory_identity = binding.get("directory_identity")
        if (type(directory_identity) is not list or len(directory_identity) != 2
                or any(type(value) is not int for value in directory_identity)):
            raise WorkspaceError("Snapshot directory identity is malformed.",
                                 code="snapshot_binding_mismatch", stage="inspect")
        try:
            # A bounded point-in-time sweep, not a lock or an atomic snapshot.
            tree = capture_tree_manifest(candidate)
        except Exception as exc:
            raise WorkspaceError("Snapshot tree could not be inspected safely.",
                                 code="snapshot_tree_inspection_failed", stage="inspect") from exc
        if tree.root_identity != tuple(directory_identity):
            raise WorkspaceError("Snapshot tree root differs from its authenticated binding.",
                                 code="snapshot_root_identity_mismatch", stage="inspect")
        # Reauthenticate after the sweep; no path-based read here holds a lock
        # over all subsequent operations and this does not claim atomicity.
        if read_binding() != binding:
            raise WorkspaceError("Snapshot binding changed during inspection.",
                                 code="snapshot_binding_changed_during_inspection", stage="inspect")
        if (binding.get("candidate_kind") != "materialized_snapshot"
                or binding.get("snapshot_scope") != "committed_tree_only"
                or binding.get("os_sandbox") is not False
                or binding.get("candidate_id") != task_id
                or not _is_sha256_hex(binding.get("manifest_digest"))
                or not _is_git_oid(binding.get("tree_oid"))
                or not _is_git_oid(binding.get("commit_oid"))):
            raise WorkspaceError("Snapshot binding does not match its Host identity.",
                                 code="snapshot_binding_mismatch", stage="inspect")
        return {
            "status": "created",
            "task_id": task_id,
            "candidate_kind": "materialized_snapshot",
            "snapshot_scope": "committed_head_tree_only",
            "candidate_path": str(candidate),
            "stable_head": str(binding["commit_oid"]),
            "candidate_head": None,
            "baseline_manifest_digest": str(binding["manifest_digest"]),
            "binding": binding,
            "os_sandbox": False,
            "merge_authorized": False,
        }

    def _inspect_bound_record(self, state: _TaskState) -> tuple[dict | None, str | None]:
        try:
            if state.workspace_record.get("candidate_kind") == "materialized_snapshot":
                record = self._inspect_snapshot_record(state.task_id)
            else:
                record = self._workspace.inspect(state.task_id)
        except WorkspaceError as exc:
            return None, exc.code
        except Exception:
            return None, "candidate_inspection_failed"
        if record != state.workspace_record:
            return None, "candidate_record_changed"
        return record, None

    def _resolve_verification_binding(self, task_id: str) -> WorkspaceBinding | None:
        state, _context, error = self._authorize_task(task_id)
        if error or state is None:
            return None
        record, failure = self._inspect_bound_record(state)
        if failure or record is None:
            return None
        candidate_id = self._candidate_identity(record)
        try:
            binding = self._trusted_verifier_resolver(
                task_id, dict(record), self._config_identity, candidate_id,
            )
        except Exception:
            return None
        if (type(binding) is not WorkspaceBinding or binding.task_id != task_id
                or not _is_sha256_hex(binding.candidate_id)
                or not _is_sha256_hex(binding.config_identity)
                or not hmac.compare_digest(binding.candidate_id, candidate_id)
                or not hmac.compare_digest(binding.config_identity, self._config_identity)
                or not isinstance(binding.source_paths, tuple)
                or binding.source_paths != self._config.verification_source_paths):
            return None
        try:
            expected_root = Path(record["candidate_path"]).resolve(strict=True)
            actual_root = Path(binding.candidate_root).resolve(strict=True)
        except (OSError, TypeError, ValueError, RuntimeError):
            return None
        if actual_root != expected_root:
            return None
        return binding

    def _candidate_identity(self, record: dict) -> str:
        if record.get("candidate_kind") == "materialized_snapshot":
            identity_record = {
                "task_id": record["task_id"],
                "candidate_kind": record["candidate_kind"],
                "candidate_path": record["candidate_path"],
                "binding": record["binding"],
            }
            identity = json.dumps(identity_record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            return hmac.new(self._key, b"self-evolution-snapshot-v1\0" + identity, hashlib.sha256).hexdigest()
        identity = json.dumps({
            "task_id": record["task_id"],
            "candidate_repository": record["candidate_repository"],
            "candidate_head": record["candidate_head"],
            "stable_head": record["stable_head"],
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._key, b"self-evolution-candidate-v1\0" + identity, hashlib.sha256).hexdigest()

    def _digest_config(self) -> str:
        payload = {
            "stable_repository": str(self._config.stable_repository),
            "trust_root": str(self._config.trust_root),
            "attribution_paths": self._config.attribution_paths,
            "verification_source_paths": self._config.verification_source_paths,
            "verification_commands": {key: list(value) for key, value in sorted(self._config.verification_commands.items())},
            "verification_timeout_seconds": float(self._config.verification_timeout_seconds),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._key, b"self-evolution-config-v1\0" + canonical, hashlib.sha256).hexdigest()


def _validated_skill_install_approval_payload(preview: object) -> tuple[dict[str, object], str]:
    """Validate and digest the exact public payload expected by skill_install.commit."""
    import hashlib

    method = getattr(preview, "approval_payload", None)
    if not callable(method):
        raise ValueError("installer preview has no approval payload")
    payload = method()
    required = {"preview_id", "source", "skill_id", "name", "target", "derived_fields",
                "skill_md", "files", "content_sha256"}
    if (type(payload) is not dict or set(payload) != required
            or type(payload["preview_id"]) is not str or not payload["preview_id"]
            or type(payload["source"]) is not dict
            or type(payload["skill_id"]) is not str
            or re.fullmatch(r"[a-z][a-z0-9-]{1,63}", payload["skill_id"]) is None
            or type(payload["name"]) is not str or type(payload["target"]) is not str
            or type(payload["derived_fields"]) is not dict or type(payload["skill_md"]) is not str
            or type(payload["files"]) is not list or not payload["files"]
            or type(payload["content_sha256"]) is not str):
        raise ValueError("installer approval payload is incomplete")
    package_material: list[bytes] = []
    seen: set[str] = set()
    has_skill_md = False
    for item in payload["files"]:
        if (type(item) is not dict or set(item) != {"path", "size", "sha256"}
                or type(item["path"]) is not str or not item["path"] or "\\" in item["path"]
                or item["path"].startswith("/")
                or any(part in {"", ".", ".."} for part in item["path"].split("/"))
                or type(item["size"]) is not int or item["size"] < 0
                or type(item["sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None):
            raise ValueError("installer approval payload has invalid file summaries")
        if item["path"] in seen:
            raise ValueError("installer approval payload has duplicate files")
        seen.add(item["path"])
        has_skill_md = has_skill_md or item["path"] == "SKILL.md"
        package_material.append(f"{item['path']}:{item['sha256']}\n".encode("utf-8"))
    if (not has_skill_md
            or hashlib.sha256(b"".join(package_material)).hexdigest() != payload["content_sha256"]):
        raise ValueError("installer approval package digest does not match its inventory")
    return payload, _approval_payload_digest(payload)


def _approval_payload_digest(payload: Mapping[str, object]) -> str:
    import hashlib
    canonical = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()



def _checked_relative_paths(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values or len(values) > 1000:
        raise ValueError(f"{label} must be a bounded non-empty tuple")
    checked: list[str] = []
    for value in values:
        if (type(value) is not str or not value or "\x00" in value or "\\" in value
                or value.startswith("/") or ":" in value
                or any(part in {"", ".", ".."} for part in value.split("/"))
                or any(part.casefold() == ".git" for part in value.split("/"))):
            raise ValueError(f"{label} contains an invalid repository-relative path")
        checked.append(value)
    if len(set(checked)) != len(checked):
        raise ValueError(f"{label} contains duplicate paths")
    return tuple(sorted(checked))


def _skill_worker_task_id(session_id: str, task_id: str, attempt_id: str,
                          run_id: str, turn_epoch: int) -> str:
    """Derive an opaque runtime key unique to one exact run/attempt identity."""
    material = json.dumps(
        [session_id, task_id, attempt_id, run_id, turn_epoch],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return "self-evolution:" + hashlib.sha256(material).hexdigest()


def _valid_identity_part(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and len(value) <= 1024 and "\x00" not in value


def _snapshot_binding_digest(binding: Mapping[str, object]) -> str:
    canonical = json.dumps(dict(binding), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _is_git_oid(value: object) -> bool:
    return type(value) is str and len(value) in {40, 64} and all(char in "0123456789abcdef" for char in value)


def _is_sha256_hex(value: object) -> bool:
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _is_canonical_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        import uuid
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _scope_attribution_evidence(evidence: dict) -> dict:
    scoped = dict(evidence)
    scoped.pop("summary_sha256", None)
    scoped["evidence_scope"] = "declared_paths_point_in_time_only"
    scoped["full_candidate_coverage"] = False
    scoped["worker_authorization_eligible"] = False
    canonical = json.dumps(scoped, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    scoped["summary_sha256"] = hashlib.sha256(canonical).hexdigest()
    return scoped


def _binding_dict(identity: tuple[str, str, int, str]) -> dict:
    return {"conversation_id": identity[0], "run_id": identity[1], "turn_epoch": identity[2],
            "request_id": identity[3], "request_origin": "human"}


def _verification_dict(result: VerificationResult) -> dict:
    return {
        "outcome": result.outcome,
        "error_code": result.error_code,
        "runtime": result.runtime,
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "candidate_binding_digest": result.candidate_binding_digest,
        "source_digest_after": result.source_digest_after,
        "source_digest_after": result.source_digest_after,
        "cwd_is_candidate_root": result.cwd_is_candidate_root,
        "process_tree_state": result.process_tree_state,
        "job_tree_state": result.job_tree_state,
        "descendants_may_be_alive": result.descendants_may_be_alive,
        "requires_reconciliation": result.requires_reconciliation,
        "automatic_retry_allowed": result.automatic_retry_allowed,
        # Preserve the verifier's containment provenance (notably Windows Job
        # evidence) while keeping the limited source-manifest scope explicit.
        "evidence_scope": result.evidence_scope,
        "full_candidate_coverage": False,
        "output_redacted": True,
    }


def _denied(code: str) -> dict:
    return {"status": "denied", "error_code": code, "worker_bound": False,
            "os_sandbox": False, "merge_authorized": False}


def _static_ast_write_path_allowed(
    path: object,
    attribution_paths: tuple[str, ...],
    verification_source_paths: tuple[str, ...],
) -> bool:
    """Exact-path authorization: experimental writes must be both attributed and statically checked."""
    return (type(path) is str and path in attribution_paths and path in verification_source_paths
            and path.lower().endswith(".py"))


def _validate_apply_path_proposal(raw: object, disclosed_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Canonicalize a same-session proposal and require exact disclosed membership."""
    if type(raw) not in {tuple, list} or not raw or len(raw) > 256:
        raise ValueError("apply_path_proposal_invalid")
    try:
        import self_evolution_manifest as manifest
        paths = tuple(raw)
        canonical = manifest.validate_relative_paths(paths)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("apply_path_proposal_invalid") from None
    if len(set(canonical)) != len(canonical) or not set(canonical).issubset(disclosed_paths):
        raise ValueError("apply_path_proposal_outside_disclosure")
    return tuple(sorted(canonical))


def _static_ast_evidence(
    baseline: snapshot_review.FrozenBaseline,
    *, candidate_id: str,
    allowed_paths: tuple[str, ...],
    expected_candidate_digest: str,
    baseline_manifest: object | None,
) -> dict[str, object]:
    """Verify syntax only for changed, explicitly allowlisted UTF-8 Python files.

    No candidate code is imported, evaluated, compiled to bytecode, or executed.
    The complete manifest is swept before and after parsing and must remain bound
    to the caller's current digest and the Host-frozen full-tree baseline.
    """
    if (type(baseline) is not snapshot_review.FrozenBaseline or not baseline.full_tree
            or baseline_manifest is None or not _is_sha256_hex(expected_candidate_digest)):
        raise ValueError("static_ast_baseline_unavailable")
    try:
        snapshot_review._validate_baseline(baseline, candidate_id)
        allowed = _checked_relative_paths(allowed_paths, "static_ast_allowlist")
    except (ValueError, TypeError, snapshot_review.ReviewError):
        raise ValueError("static_ast_baseline_or_allowlist_invalid") from None
    if not allowed or any(not item.lower().endswith(".py") for item in allowed):
        raise ValueError("static_ast_allowlist_must_be_python")
    before = snapshot_review._capture(baseline.root)
    if (before.root_identity != baseline.root_identity or not before.full_candidate_coverage
            or before.excluded_metadata or before.digest != expected_candidate_digest
            or getattr(baseline_manifest, "digest", None) != baseline.baseline_digest
            or getattr(baseline_manifest, "root_identity", None) != baseline.root_identity
            or not getattr(baseline_manifest, "full_candidate_coverage", False)
            or getattr(baseline_manifest, "excluded_metadata", ())):
        raise ValueError("static_ast_candidate_digest_unbound")
    baseline_entries = {item.path: item for item in baseline_manifest.entries}
    current_entries = {item.path: item for item in before.entries}
    changed_paths = []
    unsupported_entry_change = False
    for path in sorted(set(baseline_entries) | set(current_entries)):
        old = baseline_entries.get(path)
        current = current_entries.get(path)
        if old is None or current is None:
            changed_paths.append(path)
            unsupported_entry_change = unsupported_entry_change or (current or old).kind != "file"
            continue
        old_identity = (old.device, old.file_id)
        current_identity = (current.device, current.file_id)
        if (old.kind != current.kind or old.sha256 != current.sha256 or old.mode != current.mode
                or old.size != current.size or old_identity != current_identity
                or old.link_count != current.link_count):
            changed_paths.append(path)
            unsupported_entry_change = unsupported_entry_change or current.kind != "file"
    baseline_files = {item.path: item for item in baseline.files}
    current_files = {path: item for path, item in current_entries.items() if item.kind == "file"}
    unsupported = (unsupported_entry_change or not changed_paths or any(
        path not in allowed or not path.lower().endswith(".py") or path not in current_files
        for path in changed_paths
    ))
    source_files = ()
    if not unsupported:
        source_files = snapshot_review._collect(before, tuple(changed_paths), enforce_tree_limit=True)
        if len(source_files) != len(changed_paths):
            unsupported = True
    syntax_ok = not unsupported
    if syntax_ok:
        for item in source_files:
            if (not item.present or item.content_status != "text" or item.content is None
                    or len(item.content) > snapshot_review._MAX_FILE_BYTES):
                syntax_ok = False
                break
            try:
                source = item.content.decode("utf-8", "strict")
                ast.parse(source, filename=item.path, mode="exec", type_comments=True)
            except (UnicodeError, SyntaxError, ValueError, RecursionError):
                syntax_ok = False
                break
    after = snapshot_review._capture(baseline.root)
    if (after.root_identity != baseline.root_identity or not after.full_candidate_coverage
            or after.excluded_metadata or after.digest != before.digest
            or after.digest != expected_candidate_digest):
        raise ValueError("static_ast_candidate_changed_during_verification")
    return {
        "status": "passed" if syntax_ok else "failed",
        "verification_type": "static_ast",
        "baseline_digest": baseline.baseline_digest,
        "before_candidate_digest": before.digest,
        "after_candidate_digest": after.digest,
        "candidate_digest": after.digest,
        "evidence_scope": "full_candidate_point_in_time",
        "process_tree_state": "not_applicable",
        "requires_reconciliation": False,
        "exit_code": None,
        "changed_paths": tuple(changed_paths),
        "error_code": None if syntax_ok else ("static_ast_change_out_of_scope" if unsupported
                                               else "static_ast_syntax_or_encoding_failed"),
    }


def _failed(code: str, *, task_id: str | None = None, verification_passed: bool | None = None) -> dict:
    result = {"status": "failed", "error_code": code, "worker_bound": False,
              "os_sandbox": False, "merge_authorized": False}
    if task_id is not None:
        result["task_id"] = task_id
    if verification_passed is not None:
        result["verification_passed"] = verification_passed
    return result


def _unknown(code: str, *, task_id: str, candidate: dict | None = None,
             verification_passed: bool | None = None, evidence: dict | None = None) -> dict:
    result = {"status": "unknown", "error_code": code, "task_id": task_id,
              "worker_bound": False, "os_sandbox": False, "merge_authorized": False}
    if candidate is not None:
        result["candidate"] = candidate
    if verification_passed is not None:
        result["verification_passed"] = verification_passed
    if evidence is not None:
        result["evidence"] = evidence
    return result
