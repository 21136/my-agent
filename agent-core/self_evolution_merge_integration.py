"""Host-only selected file application from a frozen Self-Evolution review.

This module is not a renderer/tool API. Its constructor inputs, binding guard,
selection and native claim callback MUST come from authenticated Host state.
It does not authenticate a JSON receipt: a private Host/Electron bridge must
atomically consume the actual native confirmation for the exact request digest.
The current Electron v1 preview/receipt transport is not wired to this module.
The scope is Windows materialized snapshots, bounded reviewed text files, and
file-level workspace changes only. Review sweeps and writes are not atomic.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Callable

import self_evolution_manifest as manifest
import self_evolution_snapshot_review as review
import self_evolution_merge_execute as executor


class HostApplyError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NativeClaim:
    """Return value from a trusted native bridge after *consuming* its receipt.

    Constructing this object or a matching JSON payload is not authority. The
    caller must guarantee this is returned by a trusted private callback, not by
    model/renderer data. No receipt JSON is accepted by this API.
    """
    approved: bool
    request_sha256: str
    operation_id: str
    selected_paths: tuple[str, ...]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _host_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise HostApplyError("host_identity_invalid")
    return value


def _guard(callback: Callable[[], bool]) -> None:
    try:
        valid = callback() is True
    except Exception:
        valid = False
    if not valid:
        raise HostApplyError("host_binding_not_current")


def _git_blob_oid(root: Path, data: bytes) -> str:
    return os.fsdecode(executor._git_input(root, data, "hash-object", "--stdin")).strip()


def _head_file(root: Path, relative: str) -> tuple[str, str, bytes] | None:
    # Literal pathspec avoids Git pathspec interpretation, including leading ':' and glob.
    raw = executor._git(root, "--literal-pathspecs", "ls-tree", "-z", "HEAD", "--", relative)
    if not raw:
        return None
    records = raw.split(b"\0")
    if len(records) != 2 or records[1]:
        raise HostApplyError("head_entry_invalid")
    try:
        header, path = records[0].split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split(" ")
        if os.fsdecode(path) != relative or kind != "blob" or mode not in ("100644", "100755"):
            raise HostApplyError("head_entry_invalid")
    except (ValueError, UnicodeError) as exc:
        raise HostApplyError("head_entry_invalid") from exc
    return mode, oid, executor._git(root, "cat-file", "blob", oid)


def _candidate_current(baseline: review.FrozenBaseline, candidate_id: str, expected: str) -> bool:
    try:
        value = review.review_candidate(baseline, candidate_id=candidate_id, disclose_paths=())
        return (value["candidate_digest"] == expected and value["baseline_digest"] == baseline.baseline_digest
                and value["full_candidate_coverage"] is True)
    except (review.ReviewError, OSError, ValueError):
        return False


@dataclass
class HostSelectedApply:
    """Process-local, one-attempt Host capability, never serialize to a renderer."""
    _root: Path = field(repr=False)
    _baseline: review.FrozenBaseline = field(repr=False)
    _candidate_id: str
    _candidate_digest: str
    _state: executor.SelectedMergeState = field(repr=False)
    _specs: tuple[dict, ...] = field(repr=False)
    _blobs: dict[str, bytes] = field(repr=False)
    _host_guard: Callable[[], bool] = field(repr=False)
    _request: dict = field(repr=False)
    _used: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def preview(self) -> dict:
        """Return metadata for the Host's authoritative native approval preview.

        This is the Python Host contract, NOT a self_evolution_promotion_preview_v1
        or a native approval receipt. The Host must map and bind it to its existing
        Electron v1 preview/intent, and keep the original private.
        """
        return json.loads(json.dumps(self._request))

    def apply(self, *, trusted_native_claim: Callable[[object], NativeClaim]) -> executor.AppliedFileResult:
        """Consume once and apply only after a private native bridge claims approval."""
        if not callable(trusted_native_claim):
            raise HostApplyError("native_claim_bridge_required")
        with self._lock:
            if self._used:
                raise HostApplyError("host_apply_already_consumed")
            self._used = True
        _guard(self._host_guard)
        if not _candidate_current(self._baseline, self._candidate_id, self._candidate_digest):
            raise HostApplyError("candidate_changed_before_claim")
        if not executor.revalidate_selected_merge_state(self._state):
            raise HostApplyError("target_changed_before_claim")
        selected = tuple(row["path"] for row in self._request["files"])
        frozen_rows = tuple(MappingProxyType(dict(row)) for row in self._request["files"])
        request = MappingProxyType({**{key: value for key, value in self._request.items() if key != "files"},
                                    "selected_paths": selected, "files": frozen_rows})
        expected_files = tuple((row["path"], row["before_sha256"], row["after_sha256"], row["action"])
                               for row in self._request["files"])

        claim_failure = [None]

        def claim(executor_request):
            observed = tuple((row["path"], row["beforeSha256"], row["afterSha256"], row["action"])
                             for row in executor_request["files"])
            if observed != expected_files or tuple(executor_request["selectedPaths"]) != selected:
                return False
            try:
                _guard(self._host_guard)
                if not executor.revalidate_selected_merge_state(self._state):
                    return False
                if not _candidate_current(self._baseline, self._candidate_id, self._candidate_digest):
                    return False
                decision = trusted_native_claim(request)
                if (type(decision) is not NativeClaim or decision.approved is not True or
                        not hmac.compare_digest(decision.request_sha256, self._request["request_sha256"]) or
                        decision.operation_id != self._request["operation_id"] or decision.selected_paths != selected):
                    return False
                _guard(self._host_guard)
                if not _candidate_current(self._baseline, self._candidate_id, self._candidate_digest):
                    claim_failure[0] = "candidate_changed_after_approval"
                    return False
                if not executor.revalidate_selected_merge_state(self._state):
                    claim_failure[0] = "target_changed_after_approval"
                    return False
                return True
            except HostApplyError:
                claim_failure[0] = "host_binding_not_current"
                return False
            except (TypeError, ValueError):
                return False

        def execution_guard():
            try:
                _guard(self._host_guard)
                return _candidate_current(self._baseline, self._candidate_id, self._candidate_digest)
            except HostApplyError:
                return False

        result = executor.execute_selected_merge(
            self._root, self._specs, content_resolver=lambda ref: self._blobs[ref],
            trusted_host_approval_consumer=claim, trusted_execution_guard=execution_guard,
        )
        if result.status == "approval_rejected" and claim_failure[0]:
            return replace(result, status="stale_after_approval", error_code=claim_failure[0])
        return result


def prepare_host_selected_apply(*, repository_root: os.PathLike[str] | str,
                                baseline: review.FrozenBaseline,
                                session_id: str, task_id: str, attempt_id: str,
                                candidate_id: str, selected_paths: tuple[str, ...],
                                trusted_host_binding_is_current: Callable[[], bool]) -> HostSelectedApply:
    """Prepare an exact reviewed operation from trusted Host-held state only.

    ``baseline`` must be the Host's frozen full candidate baseline from before
    the worker; ``selected_paths`` must be the Host's explicit selected scope,
    not a model-provided list. The Host must hold its task lock/lease for the
    relevant lifetime and revalidate it in the guard. No stable repository is
    opened or modified until the caller supplies its chosen root and approval.
    """
    if not callable(trusted_host_binding_is_current):
        raise HostApplyError("host_binding_guard_required")
    session_id, task_id, attempt_id, candidate_id = map(_host_id, (session_id, task_id, attempt_id, candidate_id))
    _guard(trusted_host_binding_is_current)
    if type(baseline) is not review.FrozenBaseline or not baseline.full_tree or baseline.candidate_id != candidate_id:
        raise HostApplyError("host_review_baseline_invalid")
    try:
        chosen = manifest.validate_relative_paths(selected_paths)
    except (manifest.ManifestError, TypeError, ValueError) as exc:
        raise HostApplyError("selected_paths_invalid") from exc
    if not chosen or len(chosen) > 256 or tuple(selected_paths) != tuple(sorted(chosen)):
        raise HostApplyError("selected_paths_invalid")
    denied = {".git", ".venv", "venv", "node_modules", "data", "artifacts", "dist", "dist-electron",
              "__pycache__", "profile", "profiles", "user-data", "workspace", "workspaces", ".managed-runtime"}
    if any(manifest._review_path_is_sensitive(path) or
           any(part.casefold() in denied or re.search(r'[<>"|?*]', part) for part in path.split("/"))
           for path in chosen):
        raise HostApplyError("sensitive_selected_path")
    root = executor._canonical_root(repository_root)
    candidate_root = Path(baseline.root).resolve(strict=True)
    if root == candidate_root or root in candidate_root.parents or candidate_root in root.parents:
        raise HostApplyError("candidate_target_overlap")
    try:
        metadata = review.review_candidate(baseline, candidate_id=candidate_id, disclose_paths=())
        if metadata["full_candidate_coverage"] is not True:
            raise HostApplyError("candidate_review_incomplete")
        changes = {row["path"]: row for row in metadata["files"]}
        if any(path not in changes for path in chosen):
            raise HostApplyError("selected_path_not_reviewed_change")
        tree = review._capture(baseline.root)
        if tree.digest != metadata["candidate_digest"] or tree.root_identity != baseline.root_identity:
            raise HostApplyError("candidate_changed_during_prepare")
        entries = {entry.path: entry for entry in tree.entries}
        old = {item.path: item for item in baseline.files}
        specs, blobs, displayed = [], {}, []
        for path in sorted(chosen):
            row, prior = changes[path], old.get(path)
            before = _head_file(root, path)
            before_bytes = prior.content if prior and prior.present else None
            if prior and prior.present and (before_bytes is None or prior.content_status != "text"):
                raise HostApplyError("selected_content_not_reviewable")
            if before_bytes is None:
                if before is not None:
                    raise HostApplyError("candidate_baseline_target_mismatch")
            elif before is None or before[2] != before_bytes:
                raise HostApplyError("candidate_baseline_target_mismatch")
            entry = entries.get(path)
            if entry is None:
                action, after_bytes = "delete", None
                if before is None:
                    raise HostApplyError("selected_path_not_reviewed_change")
            else:
                if row["after_content_status"] != "text" or entry.kind != "file":
                    raise HostApplyError("selected_content_not_reviewable")
                after_bytes = review._read_bound_file(tree, entry)
                if sha256_bytes(after_bytes) != row["after_sha256"]:
                    raise HostApplyError("candidate_content_digest_mismatch")
                action = "modify" if before else "add"
            if action == "modify" and before_bytes == after_bytes:
                raise HostApplyError("metadata_only_change_unsupported")
            ref = f"selected-{len(specs)}" if after_bytes is not None else None
            if ref is not None:
                blobs[ref] = after_bytes
            before_mode = before[0] if before else None
            after_mode = (before_mode or "100644") if after_bytes is not None else None
            spec = {"path": path, "action": action,
                    "beforeSha256": sha256_bytes(before_bytes) if before_bytes is not None else None,
                    "afterSha256": sha256_bytes(after_bytes) if after_bytes is not None else None,
                    "beforeMode": before_mode, "afterMode": after_mode,
                    "beforeOid": before[1] if before else None,
                    "afterOid": _git_blob_oid(root, after_bytes) if after_bytes is not None else None,
                    "beforeSize": len(before_bytes) if before_bytes is not None else None,
                    "afterSize": len(after_bytes) if after_bytes is not None else None, "contentRef": ref}
            specs.append(spec)
            displayed.append({"path": path, "action": action, "before_sha256": spec["beforeSha256"],
                              "after_sha256": spec["afterSha256"], "before_size": spec["beforeSize"],
                              "after_size": spec["afterSize"], "before_mode": before_mode, "after_mode": after_mode})
        if not _candidate_current(baseline, candidate_id, metadata["candidate_digest"]):
            raise HostApplyError("candidate_changed_during_prepare")
        state = executor.capture_selected_merge_state(root, specs)
        _guard(trusted_host_binding_is_current)
    except review.ReviewError as exc:
        raise HostApplyError(exc.code) from exc
    operation_id = secrets.token_hex(16)
    payload = {"schema": "self_evolution_host_selected_apply_request_v1", "session_id": session_id,
               "task_id": task_id, "attempt_id": attempt_id, "candidate_id": candidate_id,
               "baseline_digest": baseline.baseline_digest, "candidate_digest": metadata["candidate_digest"],
               "operation_id": operation_id, "target_root": state.root, "target_head": state.head,
               "target_index_sha256": state.index_sha256, "target_worktree_digest": state.worktree_manifest_sha256,
               "files": displayed}
    payload["request_sha256"] = sha256_bytes(_canonical(payload))
    return HostSelectedApply(root, baseline, candidate_id, metadata["candidate_digest"], state,
                             tuple(specs), blobs, trusted_host_binding_is_current, payload)
