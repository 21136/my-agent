"""Build an untrusted candidate snapshot without Git checkout semantics.

Only immutable Git objects are copied into a private temporary bare repository.
All Git commands run against that repository with a scrubbed environment; tree
parsing and path creation are performed here without attributes, filters or hooks.
This is defense in depth, not an OS sandbox: callers must provide a host-owned
object directory and a destination parent not writable by an untrusted actor.
"""
from __future__ import annotations

import ctypes
import contextlib
import errno
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import sys
import subprocess
import tempfile
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

import self_evolution_manifest as _manifest


_HEX = re.compile(r"\A[0-9a-fA-F]+\Z")
_PACK = re.compile(r"\Apack-[0-9a-fA-F]{40,64}\.pack\Z")
_PACK_INDEX = re.compile(r"\Apack-[0-9a-fA-F]{40,64}\.idx\Z")
_FANOUT = re.compile(r"\A[0-9a-fA-F]{2}\Z")
_RESERVED_DOS = {"con", "prn", "aux", "nul"}
_MAX_HEADER = 256


class MaterializationError(Exception):
    """Sanitized failure with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str, publication_status: str | None = None,
                 candidate_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.publication_status = publication_status
        self.candidate_id = candidate_id


@dataclass(frozen=True)
class MaterializationResult:
    """Identity of a plain snapshot; deliberately not a Git worktree binding."""
    commit_oid: str
    destination: str
    file_count: int
    total_bytes: int
    object_count: int
    object_store_bytes: int
    candidate_kind: Literal["materialized_snapshot"]
    tree_oid: str
    manifest_digest: str
    snapshot_scope: Literal["committed_tree_only"] = "committed_tree_only"


class SafeSnapshotMaterializer:
    """Materialize regular blobs from a commit using an isolated Git object DB.

    ``object_directory`` and ``destination`` are trusted host configuration,
    never model/tool input. The source Git config, attributes, refs, alternates,
    replacement refs, hooks, filters, and worktree are never consulted.
    """

    def __init__(
        self,
        git_executable: os.PathLike[str] | str,
        *,
        max_object_store_bytes: int = 512 * 1024 * 1024,
        max_object_bytes: int = 64 * 1024 * 1024,
        max_total_bytes: int = 256 * 1024 * 1024,
        max_tree_bytes: int = 32 * 1024 * 1024,
        max_files: int = 20_000,
        max_object_files: int = 100_000,
        timeout_seconds: int = 60,
        authorized_parent_identity: tuple[int, int] | None = None,
    ) -> None:
        executable = Path(os.path.abspath(os.fspath(git_executable)))
        self._assert_regular_path(executable)
        if min(max_object_store_bytes, max_object_bytes, max_total_bytes, max_tree_bytes, max_files, max_object_files, timeout_seconds) <= 0:
            raise ValueError("Materializer limits must be positive.")
        self.git_executable = str(executable)
        self.max_object_store_bytes = max_object_store_bytes
        self.max_object_bytes = max_object_bytes
        self.max_total_bytes = max_total_bytes
        self.max_tree_bytes = max_tree_bytes
        self.max_files = max_files
        self.max_object_files = max_object_files
        self.timeout_seconds = timeout_seconds
        if authorized_parent_identity is not None:
            if (not isinstance(authorized_parent_identity, tuple) or len(authorized_parent_identity) != 2
                    or any(not isinstance(value, int) or isinstance(value, bool) or value < 0
                           for value in authorized_parent_identity)):
                raise ValueError("Authorized output parent identity must be a pair of non-negative integers.")
        self.authorized_parent_identity = authorized_parent_identity

    def materialize(
        self,
        object_directory: os.PathLike[str] | str,
        commit_oid: str,
        destination: os.PathLike[str] | str,
    ) -> MaterializationResult:
        return self._materialize(object_directory, commit_oid, destination, binding=None)

    def materialize_bound(
        self,
        object_directory: os.PathLike[str] | str,
        commit_oid: str,
        destination: os.PathLike[str] | str,
        *,
        candidate_id: str,
        integrity_key: bytes,
    ) -> MaterializationResult:
        """Create a snapshot with a durable, host-authenticated initial identity.

        The binding records the staged directory identity (stable across atomic
        publication), commit/tree OIDs, and immutable snapshot digest. It is not
        a Git worktree identity, OS sandbox, or authorization to merge.
        """
        try:
            parsed = uuid.UUID(candidate_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise MaterializationError("Candidate identifier must be a UUID.", code="invalid_candidate_id") from exc
        if str(parsed) != candidate_id:
            raise MaterializationError("Candidate identifier must be a canonical UUID.", code="invalid_candidate_id")
        if Path(os.path.abspath(os.fspath(destination))).name != candidate_id:
            raise MaterializationError("Candidate path does not match its identifier.", code="binding_identity_mismatch")
        if not isinstance(integrity_key, bytes) or len(integrity_key) < 32:
            raise MaterializationError("Host integrity key must contain at least 32 bytes.", code="invalid_integrity_key")
        return self._materialize(object_directory, commit_oid, destination,
                                 binding=(candidate_id, integrity_key))

    def read_binding(
        self, destination: os.PathLike[str] | str, *, expected_candidate_id: str, integrity_key: bytes,
        expected_parent_identity: tuple[int, int] | None = None,
    ) -> dict[str, object]:
        """Validate the persisted initial identity, not authorization for future path use.

        On Windows the local path components and binding file are held until
        validation ends; conflicting open write/delete handles are rejected.
        The returned record does not keep any handle alive: callers must not treat
        it as a guarantee that a subsequent path-based operation sees the same tree.
        """
        if not isinstance(integrity_key, bytes) or len(integrity_key) < 32:
            raise MaterializationError("Host integrity key must contain at least 32 bytes.", code="invalid_integrity_key")
        root = Path(os.path.abspath(os.fspath(destination)))
        try:
            if str(uuid.UUID(expected_candidate_id)) != expected_candidate_id:
                raise ValueError("noncanonical identifier")
        except (ValueError, TypeError, AttributeError) as exc:
            raise MaterializationError("Expected candidate identifier must be a canonical UUID.", code="invalid_candidate_id") from exc
        if root.name != expected_candidate_id:
            raise MaterializationError("Candidate path does not match its expected identifier.", code="binding_identity_mismatch")
        with contextlib.ExitStack() as stack:
            try:
                if os.name == "nt":
                    root_handle, before, actual_parent_identity = stack.enter_context(_windows_pin_binding_directory(root))
                    if expected_parent_identity is not None and actual_parent_identity != expected_parent_identity:
                        raise MaterializationError("Candidate parent is not the host-authorized directory.", code="binding_identity_mismatch")
                    binding_handle = stack.enter_context(_windows_pin_binding_file(root_handle, ".self-evolution-binding.json"))
                    raw = _windows_read_relative_file(binding_handle, ".self-evolution-binding.json", 64 * 1024)
                    after_id = _manifest._windows_handle_identity(root_handle)
                    after = (int(after_id[0]), int.from_bytes(after_id[1], "little"))
                else:
                    before = self._directory_identity(root)
                    actual_parent_identity = self._directory_identity(root.parent)
                    if expected_parent_identity is not None and actual_parent_identity != expected_parent_identity:
                        raise MaterializationError("Candidate parent is not the host-authorized directory.", code="binding_identity_mismatch")
                    binding_path = root / ".self-evolution-binding.json"
                    self._assert_no_reparse_components(binding_path)
                    info = binding_path.lstat()
                    if self._is_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                        raise MaterializationError("Candidate binding file is unsafe or oversized.", code="binding_invalid")
                    raw = binding_path.read_bytes()
                    after = self._directory_identity(root)
                    if expected_parent_identity is not None and self._directory_identity(root.parent) != expected_parent_identity:
                        raise MaterializationError("Candidate parent changed during binding read.", code="binding_identity_mismatch")
            except MaterializationError:
                raise
            except (OSError, _manifest.ManifestError) as exc:
                raise MaterializationError("Candidate binding could not be read safely.", code="binding_invalid") from exc
            if after != before:
                raise MaterializationError("Candidate directory identity changed during binding read.", code="binding_identity_mismatch")
            try:
                envelope = json.loads(raw.decode("utf-8"))
                record, signature = envelope["record"], envelope["hmac_sha256"]
                if not isinstance(record, dict):
                    raise MaterializationError("Candidate binding record is malformed.", code="binding_invalid")
                canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            except (ValueError, UnicodeError, TypeError, KeyError) as exc:
                raise MaterializationError("Candidate binding is malformed.", code="binding_invalid") from exc
            expected = hmac.new(integrity_key, canonical, hashlib.sha256).hexdigest()
            if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
                raise MaterializationError("Candidate binding authentication failed.", code="binding_tampered")
            if not isinstance(record, dict) or record.get("schema") != "self-evolution-candidate-binding.v1":
                raise MaterializationError("Candidate binding schema is unsupported.", code="binding_invalid")
            if record.get("candidate_id") != expected_candidate_id:
                raise MaterializationError("Candidate binding does not match its expected identifier.", code="binding_identity_mismatch")
            if record.get("candidate_path_digest") != self._candidate_path_digest(root):
                raise MaterializationError("Candidate path no longer matches its authenticated identity.", code="binding_identity_mismatch")
            bound_parent = record.get("authorized_parent_identity")
            if (not isinstance(bound_parent, list) or len(bound_parent) != 2
                    or any(not isinstance(value, int) or isinstance(value, bool) for value in bound_parent)):
                raise MaterializationError("Candidate parent identity binding is malformed.", code="binding_invalid")
            if expected_parent_identity is not None and bound_parent != list(expected_parent_identity):
                raise MaterializationError("Candidate is not under the host-authorized parent identity.", code="binding_identity_mismatch")
            if record.get("directory_identity") != list(after):
                raise MaterializationError("Candidate directory no longer matches its authenticated identity.", code="binding_identity_mismatch")
            return record

    def _materialize(
        self,
        object_directory: os.PathLike[str] | str,
        commit_oid: str,
        destination: os.PathLike[str] | str,
        *,
        binding: tuple[str, bytes] | None,
    ) -> MaterializationResult:
        object_dir = Path(os.path.abspath(os.fspath(object_directory)))
        output = Path(os.path.abspath(os.fspath(destination)))
        hash_name = self._validate_oid(commit_oid)
        self._assert_directory_path(object_dir)
        parent_identity = self._directory_identity(output.parent)
        if not self._cleanup_is_race_safe():
            raise MaterializationError(
                "Temporary snapshot cleanup cannot be proven safe on this platform.",
                code="unsafe_cleanup_unavailable",
            )
        if self.authorized_parent_identity is None:
            # Compatibility for isolated tests/subclasses only. The exact
            # production class requires the host-supplied identity captured by
            # SelfEvolutionWorkspace.
            if not self._cleanup_is_race_safe():
                raise MaterializationError(
                    "Output parent has no host-authorized identity.", code="unauthorized_parent"
                )
            authorized_parent_identity = parent_identity
        else:
            authorized_parent_identity = self.authorized_parent_identity
        if parent_identity != authorized_parent_identity:
            raise MaterializationError(
                "Output parent does not match the host-authorized directory identity.",
                code="unauthorized_parent",
            )
        if output.exists() or output.is_symlink():
            raise MaterializationError("Destination already exists.", code="destination_exists")
        if self._same_or_nested(object_dir, output) or self._same_or_nested(output, object_dir):
            raise MaterializationError("Object directory and destination must be disjoint.", code="path_overlap")

        scratch = _create_temporary_tree(output.parent, expected_parent_identity=parent_identity)
        temp_root = scratch.path
        temp_root_identity = scratch.root_identity
        published = False
        try:
            safe_git = temp_root / "git"
            template = temp_root / "empty-template"
            template.mkdir()
            self._run_git(
                ["init", "--bare", "--quiet", f"--object-format={hash_name}", f"--template={template}", str(safe_git)],
                cwd=temp_root,
            )
            safe_objects = safe_git / "objects"
            copied_bytes = self._snapshot_objects(object_dir, safe_objects, hash_name)
            hooks = temp_root / "empty-hooks"
            hooks.mkdir()
            attrs = temp_root / "empty-attributes"
            attrs.write_bytes(b"")
            candidate_stage = temp_root / "tree"
            candidate_stage.mkdir()
            candidate_stage_identity = self._directory_identity(candidate_stage)

            reader = _ObjectReader(self, safe_git, temp_root, hooks, attrs)
            stage_handle = None
            try:
                stage_handle = _WindowsDirectoryHandle.open(candidate_stage) if os.name == "nt" else None
                tree_oid, entries = self._read_commit_tree(reader, commit_oid, hash_name)
                total = 0
                manifest_entries: list[dict[str, object]] = []
                for relative, oid, mode in entries:
                    blob = reader.read_object(oid, "blob", self.max_object_bytes)
                    total += len(blob)
                    if total > self.max_total_bytes:
                        raise MaterializationError("Snapshot exceeds the total file-size limit.", code="total_too_large")
                    target = candidate_stage.joinpath(*relative.split("/"))
                    if os.name == "nt" and len(str(target).encode("utf-16-le")) // 2 >= 240:
                        raise MaterializationError("Materialized path exceeds the conservative Windows path limit.", code="unsafe_path")
                    if os.name == "nt":
                        if stage_handle is None:
                            raise MaterializationError("Staging directory handle is unavailable.", code="safe_handle_unavailable")
                        _windows_create_path_relative(stage_handle._handle, relative.split("/"), blob)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self._assert_no_reparse_components(target.parent)
                        with target.open("xb") as stream:
                            stream.write(blob)
                    manifest_entries.append({
                        "path": relative,
                        "mode": mode.decode("ascii"),
                        "git_oid": oid.lower(),
                        "sha256": hashlib.sha256(blob).hexdigest(),
                        "size": len(blob),
                    })
                    if mode == b"100755" and os.name != "nt":
                        try:
                            target.chmod(0o755)
                        except OSError:
                            pass
                reader.close()
                if self._directory_identity(output.parent) != parent_identity:
                    raise MaterializationError("Destination parent identity changed during materialization.", code="parent_identity_changed")
                self._assert_no_reparse_components(output.parent)
                if output.exists() or output.is_symlink():
                    raise MaterializationError("Destination appeared during materialization.", code="destination_race")
                identity_payload = {
                    "schema": "self-evolution-materialized-snapshot.v1",
                    "commit_oid": commit_oid.lower(),
                    "tree_oid": tree_oid.lower(),
                    "files": sorted(manifest_entries, key=lambda item: str(item["path"]).encode("utf-8")),
                }
                manifest_digest = hashlib.sha256(
                    json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if binding is not None:
                    candidate_id, integrity_key = binding
                    binding_record: dict[str, object] = {
                        "schema": "self-evolution-candidate-binding.v1",
                        "candidate_id": candidate_id,
                        "candidate_path_digest": self._candidate_path_digest(output),
                        "authorized_parent_identity": list(authorized_parent_identity),
                        "commit_oid": commit_oid.lower(),
                        "tree_oid": tree_oid.lower(),
                        "manifest_digest": manifest_digest,
                        "directory_identity": list(candidate_stage_identity),
                        "candidate_kind": "materialized_snapshot",
                        "snapshot_scope": "committed_tree_only",
                        "os_sandbox": False,
                    }
                    canonical = json.dumps(binding_record, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    envelope = {"record": binding_record,
                                "hmac_sha256": hmac.new(integrity_key, canonical, hashlib.sha256).hexdigest()}
                    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    if os.name == "nt":
                        if stage_handle is None:
                            raise MaterializationError("Staging directory handle is unavailable.", code="safe_handle_unavailable")
                        _windows_create_file_relative(stage_handle._handle, ".self-evolution-binding.json", payload)
                    else:
                        binding_path = candidate_stage / ".self-evolution-binding.json"
                        with binding_path.open("xb") as stream:
                            stream.write(payload)
                            stream.flush()
                            os.fsync(stream.fileno())
                _publish_directory_noreplace(
                    candidate_stage, output, expected_parent_identity=parent_identity,
                    expected_source_identity=candidate_stage_identity,
                    pinned_parent_handle=scratch.parent_handle,
                )
                published = True
                return MaterializationResult(
                    commit_oid=commit_oid.lower(), destination=str(output), file_count=len(entries),
                    total_bytes=total, object_count=reader.object_count, object_store_bytes=copied_bytes,
                    candidate_kind="materialized_snapshot", tree_oid=tree_oid.lower(), manifest_digest=manifest_digest,
                )
            finally:
                reader.close()
                if stage_handle is not None:
                    stage_handle.close()
        except MaterializationError:
            raise
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise MaterializationError("Snapshot materialization failed closed.", code="materialization_failed") from exc
        finally:
            try:
                _cleanup_temporary_tree(
                    temp_root, expected_parent_identity=scratch.parent_identity,
                    expected_root_identity=temp_root_identity,
                    parent_handle=scratch.parent_handle, root_handle=scratch.root_handle,
                )
            except (MaterializationError, OSError) as exc:
                if published:
                    raise MaterializationError(
                        "Publication requires host recovery after temporary cleanup failed; preserve all remaining data.",
                        code="publication_unknown", publication_status="unknown",
                        candidate_id=binding[0] if binding is not None else None,
                    ) from exc
                if isinstance(exc, MaterializationError):
                    raise
                raise MaterializationError(
                    "Temporary materialization data could not be safely cleaned; preserve remaining data.",
                    code="temporary_cleanup_failed",
                ) from exc
            finally:
                scratch.close()

    @staticmethod
    def _candidate_path_digest(path: Path) -> str:
        normalized = os.path.normcase(os.path.abspath(os.fspath(path)))
        return hashlib.sha256(os.fsencode(normalized)).hexdigest()

    def _cleanup_is_race_safe(self) -> bool:
        """Allow production only where host identity and handle-relative lifecycle exist.

        The host captures the configured trust-root identity when constructing
        its workspace. Windows scratch creation pins the complete local ancestor
        chain, cleanup is handle-relative, and publication can reuse that exact
        pinned parent. Other platforms remain fail-closed until equivalent
        handle-relative create, publish, and cleanup are implemented.
        """
        return os.name == "nt" and self.authorized_parent_identity is not None

    def _read_commit_tree(self, reader: "_ObjectReader", commit_oid: str, hash_name: str) -> tuple[str, list[tuple[str, str, bytes]]]:
        commit = reader.read_object(commit_oid, "commit", min(self.max_tree_bytes, 4 * 1024 * 1024))
        headers = commit.split(b"\n\n", 1)[0].splitlines()
        tree_headers = [line[5:] for line in headers if line.startswith(b"tree ")]
        if len(tree_headers) != 1:
            raise MaterializationError("Commit has an invalid tree header.", code="invalid_commit")
        try:
            root_tree = tree_headers[0].decode("ascii")
        except UnicodeDecodeError as exc:
            raise MaterializationError("Commit tree id is invalid.", code="invalid_commit") from exc
        self._validate_oid(root_tree)
        oid_bytes = 20 if hash_name == "sha1" else 32
        pending: list[tuple[str, str]] = [("", root_tree)]
        files: list[tuple[str, str, bytes]] = []
        nodes: dict[str, str] = {}
        total_tree_bytes = 0
        visited_trees = 0
        while pending:
            visited_trees += 1
            if visited_trees > self.max_files * 2 + 1024:
                raise MaterializationError("Snapshot tree graph exceeds the node limit.", code="too_many_tree_nodes")
            prefix, tree_oid = pending.pop()
            raw = reader.read_object(tree_oid, "tree", self.max_tree_bytes)
            total_tree_bytes += len(raw)
            if total_tree_bytes > self.max_tree_bytes:
                raise MaterializationError("Snapshot tree data exceeds the configured limit.", code="tree_data_too_large")
            offset = 0
            while offset < len(raw):
                space = raw.find(b" ", offset)
                nul = raw.find(b"\0", space + 1) if space >= 0 else -1
                if space < 0 or nul < 0 or nul + 1 + oid_bytes > len(raw):
                    raise MaterializationError("Tree object is malformed.", code="invalid_tree")
                mode = raw[offset:space]
                name_raw = raw[space + 1:nul]
                child_oid = raw[nul + 1:nul + 1 + oid_bytes].hex()
                offset = nul + 1 + oid_bytes
                name = self._validate_component(name_raw)
                relative = f"{prefix}/{name}" if prefix else name
                directory = mode == b"40000"
                if mode not in (b"40000", b"100644", b"100755"):
                    code = "symlink_forbidden" if mode == b"120000" else "gitlink_forbidden" if mode == b"160000" else "unsupported_tree_mode"
                    raise MaterializationError("Snapshot contains a non-regular or unsupported Git entry.", code=code)
                self._register_path(nodes, relative, "directory" if directory else "file")
                if directory:
                    pending.append((relative, child_oid))
                else:
                    files.append((relative, child_oid, mode))
                    if len(files) > self.max_files:
                        raise MaterializationError("Snapshot has too many files.", code="too_many_files")
        return root_tree.lower(), files

    @staticmethod
    def _register_path(nodes: dict[str, str], relative: str, kind: str) -> None:
        parts = relative.split("/")
        for index in range(1, len(parts) + 1):
            key = "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts[:index])
            node_kind = kind if index == len(parts) else "directory"
            prior = nodes.get(key)
            is_explicit_leaf = index == len(parts)
            if prior is not None and (prior != node_kind or is_explicit_leaf):
                raise MaterializationError("Tree contains colliding Windows paths.", code="path_collision")
            nodes[key] = node_kind

    @staticmethod
    def _validate_component(raw: bytes) -> str:
        try:
            name = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MaterializationError("Tree path is not valid UTF-8.", code="unsafe_path") from exc
        if not name or name in (".", "..") or name.casefold() == ".git":
            raise MaterializationError("Tree contains a reserved path component.", code="unsafe_path")
        if name[-1] in (".", " ") or any(ord(char) < 32 or char in '<>:"|?*\\/' for char in name):
            raise MaterializationError("Tree contains a Windows-unsafe path component.", code="unsafe_path")
        stem = name.split(".", 1)[0].rstrip(" .").casefold()
        if stem in _RESERVED_DOS or re.fullmatch(r"(?:com|lpt)[1-9\u00b9\u00b2\u00b3]", stem):
            raise MaterializationError("Tree contains a reserved Windows device name.", code="unsafe_path")
        if len(name.encode("utf-8")) > 240:
            raise MaterializationError("Tree path component exceeds the supported limit.", code="unsafe_path")
        return name

    @staticmethod
    def _validate_oid(oid: str) -> str:
        if not isinstance(oid, str) or len(oid) not in (40, 64) or not _HEX.fullmatch(oid):
            raise MaterializationError("Object id must be a full SHA-1 or SHA-256 hex id.", code="invalid_object_id")
        return "sha1" if len(oid) == 40 else "sha256"

    def _snapshot_objects(self, source: Path, target: Path, hash_name: str) -> int:
        total = 0
        object_file_count = 0
        fanouts = [entry for entry in source.iterdir() if _FANOUT.fullmatch(entry.name)]
        for fanout in fanouts:
            info = fanout.lstat()
            if self._is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise MaterializationError("Object fanout is linked or not a directory.", code="unsafe_object_store")
            dest_dir = target / fanout.name.lower()
            dest_dir.mkdir(exist_ok=True)
            for obj in fanout.iterdir():
                object_file_count += 1
                if object_file_count > self.max_object_files:
                    raise MaterializationError("Object store has too many entries.", code="too_many_object_files")
                obj_info = obj.lstat()
                if self._is_reparse(obj_info) or not stat.S_ISREG(obj_info.st_mode):
                    raise MaterializationError("Object entry is linked or not a regular file.", code="unsafe_object_store")
                if len(obj.name) != (38 if hash_name == "sha1" else 62) or not _HEX.fullmatch(obj.name):
                    continue
                total += self._copy_bounded(obj, dest_dir / obj.name.lower(), total)
        pack_dir = source / "pack"
        if pack_dir.exists():
            info = pack_dir.lstat()
            if self._is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise MaterializationError("Pack directory is linked or invalid.", code="unsafe_object_store")
            target_pack = target / "pack"
            target_pack.mkdir(exist_ok=True)
            pack_files = list(pack_dir.iterdir())
            object_file_count += len(pack_files)
            if object_file_count > self.max_object_files:
                raise MaterializationError("Object store has too many entries.", code="too_many_object_files")
            for item in pack_files:
                item_info = item.lstat()
                if self._is_reparse(item_info) or not stat.S_ISREG(item_info.st_mode):
                    raise MaterializationError("Pack entry is linked or invalid.", code="unsafe_object_store")
            names = {item.name for item in pack_files}
            for item in pack_files:
                if _PACK.fullmatch(item.name):
                    if len(item.name) != (50 if hash_name == "sha1" else 74):
                        raise MaterializationError("Pack filename uses the wrong object format.", code="invalid_pack")
                    expected_idx = item.name[:-5] + ".idx"
                    if expected_idx not in names:
                        raise MaterializationError("Pack index is missing.", code="invalid_pack")
                    total += self._copy_bounded(item, target_pack / item.name.lower(), total)
                elif _PACK_INDEX.fullmatch(item.name):
                    if len(item.name) != (49 if hash_name == "sha1" else 73):
                        raise MaterializationError("Pack index filename uses the wrong object format.", code="invalid_pack")
                    expected_pack = item.name[:-4] + ".pack"
                    if expected_pack not in names:
                        raise MaterializationError("Pack data is missing.", code="invalid_pack")
                    total += self._copy_bounded(item, target_pack / item.name.lower(), total)
        return total

    def _copy_bounded(self, source: Path, destination: Path, current_total: int) -> int:
        info = source.lstat()
        if self._is_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise MaterializationError("Object source changed to a non-regular file.", code="unsafe_object_store")
        size = info.st_size
        if size < 0 or current_total + size > self.max_object_store_bytes:
            raise MaterializationError("Object store exceeds the configured snapshot limit.", code="object_store_too_large")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(source, flags)
        try:
            opened = os.fstat(fd)
            if self._is_reparse(opened) or not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, size):
                raise MaterializationError("Object source changed during safe open.", code="object_store_race")
            with os.fdopen(fd, "rb", closefd=False) as src, destination.open("xb") as dst:
                remaining = size
                while remaining:
                    chunk = src.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise MaterializationError("Object source changed while being copied.", code="object_store_race")
                    dst.write(chunk)
                    remaining -= len(chunk)
                if src.read(1):
                    raise MaterializationError("Object source grew while being copied.", code="object_store_race")
            return size
        finally:
            os.close(fd)

    def _run_git(self, args: list[str], *, cwd: Path, git_dir: Path | None = None,
                 hooks: Path | None = None, attrs: Path | None = None) -> bytes:
        command = [self.git_executable]
        if git_dir is not None:
            command += [f"-c", f"core.hooksPath={hooks}", "-c", f"core.attributesFile={attrs}",
                        "-c", "core.fsmonitor=false", "-c", "core.quotePath=false",
                        f"--git-dir={git_dir}"]
        command += args
        env = self._safe_environment()
        try:
            proc = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=self.timeout_seconds, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MaterializationError("Trusted Git plumbing could not be completed.", code="git_plumbing_failed") from exc
        if proc.returncode:
            raise MaterializationError("Trusted Git plumbing rejected the object snapshot.", code="git_plumbing_failed")
        return proc.stdout

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
                    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TEMPLATE_DIR": os.devnull})
        return env

    @staticmethod
    def _assert_regular_path(path: Path) -> None:
        SafeSnapshotMaterializer._assert_no_reparse_components(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Git executable must be a regular file.")

    @staticmethod
    def _directory_identity(path: Path) -> tuple[int, int]:
        """Return a non-reparse identity, sourced from a Windows handle when available.

        This detects final-component replacement between identity checks; it is
        not a lock and does not make subsequent path operations handle-relative.
        """
        SafeSnapshotMaterializer._assert_no_reparse_components(path)
        if os.name == "nt":
            with _WindowsDirectoryHandle.open(path) as directory:
                return directory.identity
        try:
            info = path.lstat()
        except OSError as exc:
            raise MaterializationError("Configured directory identity could not be read.", code="invalid_directory") from exc
        if SafeSnapshotMaterializer._is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise MaterializationError("Configured path is not a plain directory.", code="invalid_directory")
        return int(info.st_dev), int(info.st_ino)

    @staticmethod
    def _assert_directory_path(path: Path) -> None:
        SafeSnapshotMaterializer._assert_no_reparse_components(path)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise MaterializationError("Configured path is not a directory.", code="invalid_directory")

    @staticmethod
    def _assert_no_reparse_components(path: Path) -> None:
        current = Path(path.anchor)
        for component in path.parts[1:]:
            current = current / component
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if SafeSnapshotMaterializer._is_reparse(info):
                raise MaterializationError("Path contains a symbolic link or reparse point.", code="linked_path")

    @staticmethod
    def _is_reparse(info: os.stat_result) -> bool:
        attrs = getattr(info, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return stat.S_ISLNK(info.st_mode) or bool(attrs & reparse)

    @staticmethod
    def _same_or_nested(left: Path, right: Path) -> bool:
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False


class _WindowsDirectoryHandle:
    """Pinned identity for one opened Windows directory object.

    The handle opens the final path component with OPEN_REPARSE_POINT and
    verifies its attributes and file ID from that handle. It also supports
    handle-relative no-replace publication, but does not make ancestor
    traversal or recursive cleanup handle-relative.
    """

    _GENERIC_READ_ATTRIBUTES = 0x0080
    _FILE_ADD_SUBDIRECTORY = 0x0004
    _FILE_TRAVERSE = 0x0020
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_ID_INFO_CLASS = 18
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9

    class _FileIdInfo(ctypes.Structure):
        _fields_ = [("volume_serial_number", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("file_attributes", ctypes.c_ulong), ("reparse_tag", ctypes.c_ulong)]

    class _FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", ctypes.c_void_p),
            ("file_name_length", ctypes.c_uint32),
            ("file_name", ctypes.c_wchar * 1),
        ]

    def __init__(self, handle: int, identity: tuple[int, int], kernel32: object, *, owns_handle: bool = True) -> None:
        self._handle = handle
        self.identity = identity
        self._kernel32 = kernel32
        self._owns_handle = owns_handle

    @classmethod
    def open(
        cls, path: os.PathLike[str] | str, *, desired_access: int | None = None
    ) -> "_WindowsDirectoryHandle":
        if os.name != "nt":
            raise MaterializationError(
                "Windows directory handles are unavailable on this platform.", code="safe_handle_unavailable"
            )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        )
        create_file.restype = ctypes.c_void_p
        handle = create_file(
            os.path.abspath(os.fspath(path)),
            cls._GENERIC_READ_ATTRIBUTES if desired_access is None else desired_access,
            cls._FILE_SHARE_READ | cls._FILE_SHARE_WRITE | cls._FILE_SHARE_DELETE,
            None, cls._OPEN_EXISTING,
            cls._FILE_FLAG_BACKUP_SEMANTICS | cls._FILE_FLAG_OPEN_REPARSE_POINT, None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise MaterializationError(
                "Configured directory could not be opened by handle.", code="invalid_directory"
            ) from ctypes.WinError(ctypes.get_last_error())

        get_info = kernel32.GetFileInformationByHandleEx
        get_info.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        get_info.restype = ctypes.c_int
        try:
            attributes = cls._FileAttributeTagInfo()
            if not get_info(handle, cls._FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(attributes), ctypes.sizeof(attributes)):
                raise MaterializationError(
                    "Directory attributes could not be read from its handle.", code="invalid_directory"
                ) from ctypes.WinError(ctypes.get_last_error())
            if attributes.file_attributes & cls._FILE_ATTRIBUTE_REPARSE_POINT:
                raise MaterializationError("Configured path is a reparse point.", code="linked_path")
            if not attributes.file_attributes & cls._FILE_ATTRIBUTE_DIRECTORY:
                raise MaterializationError("Configured path is not a directory.", code="invalid_directory")

            file_id = cls._FileIdInfo()
            if not get_info(handle, cls._FILE_ID_INFO_CLASS, ctypes.byref(file_id), ctypes.sizeof(file_id)):
                raise MaterializationError(
                    "Directory identity could not be read from its handle.", code="invalid_directory"
                ) from ctypes.WinError(ctypes.get_last_error())
            identity = (int(file_id.volume_serial_number), int.from_bytes(bytes(file_id.file_id), "little"))
            return cls(handle, identity, kernel32)
        except BaseException:
            close_handle(handle)
            raise

    def rename_handle_noreplace(self, source: "_WindowsDirectoryHandle", name: str) -> None:
        """Rename an already-open directory into this pinned directory handle.

        The destination is a single relative child name. Windows resolves it
        against RootDirectory and ReplaceIfExists is false, so neither parent
        path replacement nor an occupied destination can redirect/clobber it.
        """
        if self._handle is None or source._handle is None:
            raise MaterializationError("Directory handle is closed.", code="safe_handle_unavailable")
        if not name or name in {".", ".."} or any(char in name for char in "\\/:\0"):
            raise MaterializationError("Publication name must be one plain child component.", code="unsafe_path")
        encoded = name.encode("utf-16-le")
        layout = self._FileRenameInformation
        name_offset = layout.file_name.offset
        buffer_size = name_offset + len(encoded)
        buffer = ctypes.create_string_buffer(buffer_size)
        info = ctypes.cast(buffer, ctypes.POINTER(layout)).contents
        info.replace_if_exists = 0
        info.root_directory = self._handle
        info.file_name_length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded, len(encoded))

        ntdll = ctypes.WinDLL("ntdll")

        class _IoStatusBlock(ctypes.Structure):
            _fields_ = [("status_or_pointer", ctypes.c_void_p), ("information", ctypes.c_size_t)]

        rename = ntdll.NtSetInformationFile
        rename.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_int,
        )
        rename.restype = ctypes.c_long
        io_status = _IoStatusBlock()
        status = int(rename(
            source._handle, ctypes.byref(io_status), ctypes.byref(buffer),
            buffer_size, 10,  # FileRenameInformation (native FILE_INFORMATION_CLASS)
        ))
        unsigned_status = status & 0xFFFFFFFF
        if status < 0:
            if unsigned_status in (0xC0000035, 0xC000003A):  # STATUS_OBJECT_NAME_COLLISION / NOT_FOUND
                raise MaterializationError("Destination appeared during materialization.", code="destination_race")
            raise MaterializationError(
                "Handle-relative directory publication failed.", code="safe_publish_failed"
            ) from OSError(f"NtSetInformationFile failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}")

    def close(self) -> None:
        if self._handle is not None and self._owns_handle:
            self._kernel32.CloseHandle(self._handle)
        self._handle = None

    def __enter__(self) -> "_WindowsDirectoryHandle":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


@contextlib.contextmanager
def _windows_nt_open_relative(
    parent_handle: int, name: str, *, access: int, share: int, disposition: int, options: int,
):
    """Open/create one child relative to a held directory without following reparse points."""
    if os.name != "nt":
        raise MaterializationError("Windows relative file operations are unavailable.", code="safe_handle_unavailable")
    if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\x00", ":")):
        raise MaterializationError("Unsafe relative path component.", code="unsafe_path")
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusOrPointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    encoded_length = len(name.encode("utf-16-le"))
    if encoded_length > 65532:
        raise MaterializationError("Relative path component is too long.", code="unsafe_path")
    name_buffer = ctypes.create_unicode_buffer(name)
    name_descriptor = UnicodeString(encoded_length, encoded_length + 2, ctypes.cast(name_buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent_handle,
                                  ctypes.pointer(name_descriptor), 0x40, None, None)
    io, handle = IoStatusBlock(), wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll")
    create = ntdll.NtCreateFile
    create.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(ObjectAttributes),
                       ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
                       wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    create.restype = ctypes.c_long
    status = int(create(ctypes.byref(handle), access, ctypes.byref(attributes), ctypes.byref(io), None, 0,
                        share, disposition, options | 0x00200000 | 0x20, None, 0))
    if status < 0:
        unsigned = status & 0xFFFFFFFF
        if disposition == 2 and unsigned == 0xC0000035:  # STATUS_OBJECT_NAME_COLLISION
            raise MaterializationError("Destination already exists.", code="destination_exists")
        raise MaterializationError("Handle-relative file operation failed.", code="safe_file_open_failed") from OSError(
            f"NtCreateFile failed with NTSTATUS 0x{unsigned:08x}"
        )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        identity, info = _manifest._windows_handle_info(handle.value)
        if info["attributes"] & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise MaterializationError("Reparse points are not accepted.", code="linked_path")
        yield int(handle.value), info
    finally:
        kernel.CloseHandle(handle)


def _windows_create_directory_relative(parent_handle: int, name: str) -> int:
    with _windows_nt_open_relative(parent_handle, name, access=0x00100080, share=0x3,
                                   disposition=3, options=0x1) as (handle, info):  # FILE_OPEN_IF | DIRECTORY_FILE
        if not info["directory"]:
            raise MaterializationError("Path component is not a directory.", code="unsafe_path")
        # Return a newly opened independent handle; the caller owns it.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        duplicate = kernel.DuplicateHandle
        duplicate.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        duplicate.restype = ctypes.c_int
        current = kernel.GetCurrentProcess()
        copied = ctypes.c_void_p()
        if not duplicate(current, handle, current, ctypes.byref(copied), 0, False, 0x2):
            raise MaterializationError("Directory handle could not be retained.", code="safe_handle_unavailable") from ctypes.WinError(ctypes.get_last_error())
        return int(copied.value)


def _windows_create_file_relative(parent_handle: int, name: str, payload: bytes) -> None:
    """Create a new regular file, write/flush it, then verify exact bytes via its handle."""
    from ctypes import wintypes
    with _windows_nt_open_relative(parent_handle, name, access=0x0012019F, share=0x1,
                                   disposition=2, options=0x0) as (handle, info):  # FILE_CREATE
        if info["directory"]:
            raise MaterializationError("File path is a directory.", code="unsafe_path")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        write_file, read_file = kernel.WriteFile, kernel.ReadFile
        write_file.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
        write_file.restype = wintypes.BOOL
        read_file.argtypes = write_file.argtypes
        read_file.restype = wintypes.BOOL
        flush = kernel.FlushFileBuffers
        flush.argtypes = (wintypes.HANDLE,)
        flush.restype = wintypes.BOOL
        seek = kernel.SetFilePointerEx
        seek.argtypes = (wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD)
        seek.restype = wintypes.BOOL
        if payload:
            source = ctypes.create_string_buffer(payload)
            offset = 0
            while offset < len(payload):
                transferred = wintypes.DWORD()
                remaining = min(1024 * 1024, len(payload) - offset)
                if not write_file(handle, ctypes.byref(source, offset), remaining, ctypes.byref(transferred), None):
                    raise MaterializationError("Candidate file write failed.", code="safe_file_write_failed") from ctypes.WinError(ctypes.get_last_error())
                if transferred.value == 0:
                    raise MaterializationError("Candidate file write made no progress.", code="safe_file_write_failed")
                offset += transferred.value
        if not flush(handle):
            raise MaterializationError("Candidate file flush failed.", code="safe_file_write_failed") from ctypes.WinError(ctypes.get_last_error())
        zero = ctypes.c_longlong()
        if not seek(handle, 0, ctypes.byref(zero), 0):
            raise MaterializationError("Candidate file readback seek failed.", code="safe_file_readback_failed") from ctypes.WinError(ctypes.get_last_error())
        actual = bytearray()
        while len(actual) < len(payload):
            block = ctypes.create_string_buffer(min(1024 * 1024, len(payload) - len(actual)))
            transferred = wintypes.DWORD()
            if not read_file(handle, block, len(block), ctypes.byref(transferred), None):
                raise MaterializationError("Candidate file readback failed.", code="safe_file_readback_failed") from ctypes.WinError(ctypes.get_last_error())
            if transferred.value == 0:
                break
            actual.extend(block.raw[:transferred.value])
        if bytes(actual) != payload:
            raise MaterializationError("Candidate file readback did not match the written bytes.", code="safe_file_readback_mismatch")


def _windows_create_path_relative(root_handle: int, components: list[str], payload: bytes) -> None:
    if not components:
        raise MaterializationError("File path is empty.", code="unsafe_path")
    held: list[int] = []
    parent = root_handle
    try:
        for component in components[:-1]:
            parent = _windows_create_directory_relative(parent, component)
            held.append(parent)
        _windows_create_file_relative(parent, components[-1], payload)
    finally:
        if held:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            for handle in reversed(held):
                kernel.CloseHandle(handle)


@contextlib.contextmanager
def _windows_pin_binding_file(parent_handle: int, name: str):
    """Open the file relative to its pinned parent, denying write/delete sharing."""
    try:
        with _windows_nt_open_relative(parent_handle, name, access=0x00100081, share=0x1,
                                       disposition=1, options=0x40) as (handle, info):
            if info["directory"] or info["delete_pending"]:
                raise MaterializationError("Candidate binding file is unsafe.", code="binding_invalid")
            yield handle
    except MaterializationError as exc:
        if exc.code == "safe_file_open_failed":
            raise MaterializationError("Candidate binding could not be safely opened.", code="binding_invalid") from exc
        raise


def _windows_read_relative_file(handle: int, name: str, max_bytes: int) -> bytes:
    """Read from an already pinned binding handle; the caller retains ownership."""
    if name != ".self-evolution-binding.json":
        raise MaterializationError("Unexpected binding file name.", code="binding_invalid")
    identity, info = _manifest._windows_handle_info(handle)
    size = info.get("size")
    if not isinstance(size, int) or size < 0 or size > max_bytes:
        raise MaterializationError("Candidate binding file is unsafe or oversized.", code="binding_invalid")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    read_file = kernel.ReadFile
    read_file.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    read_file.restype = wintypes.BOOL
    chunks = bytearray()
    while len(chunks) < size:
        block = ctypes.create_string_buffer(min(64 * 1024, size - len(chunks)))
        transferred = wintypes.DWORD()
        if not read_file(handle, block, len(block), ctypes.byref(transferred), None):
            raise MaterializationError("Candidate binding could not be read safely.", code="binding_invalid") from ctypes.WinError(ctypes.get_last_error())
        if transferred.value == 0:
            break
        chunks.extend(block.raw[:transferred.value])
    if len(chunks) != size:
        raise MaterializationError("Candidate binding changed during readback.", code="binding_invalid")
    return bytes(chunks)


@contextlib.contextmanager
def _windows_pin_binding_directory(path: Path):
    """Hold each local path component without delete sharing through binding validation.

    Handles protect this operation only: closing them does not bind future path use.
    The volume-root helper rejects remote, SUBST and unsupported root aliases.
    """
    raw = os.fspath(path)
    if not path.is_absolute() or not re.match(r"^[A-Za-z]:\\", raw):
        raise MaterializationError("Binding path requires a local absolute drive path.", code="binding_invalid")
    components = path.parts[1:]
    if not components or any(part in (".", "..") or part.endswith((".", " ")) or
                             any(c in part for c in ("/", "\\", "\x00", ":"))
                             for part in components):
        raise MaterializationError("Binding path contains an unsupported component.", code="binding_invalid")
    try:
        with contextlib.ExitStack() as stack:
            current, _ = stack.enter_context(_manifest._windows_open_volume_root(path.drive))
            info = None
            parent_identity = None
            for index, component in enumerate(components):
                if index == len(components) - 1:
                    parent_volume, parent_file_id = _manifest._windows_handle_identity(current)
                    parent_identity = (int(parent_volume), int.from_bytes(parent_file_id, "little"))
                # FILE_LIST_DIRECTORY is essential: Windows may permit a rename
                # despite denied delete sharing on an attributes-only handle.
                current, info = stack.enter_context(_windows_nt_open_relative(
                    current, component, access=0x00100081, share=0x3,
                    disposition=1, options=0x1,
                ))
                if not info["directory"] or info["delete_pending"]:
                    raise MaterializationError("Binding path is not a safe directory.", code="binding_invalid")
            volume, file_id = _manifest._windows_handle_identity(current)
            yield current, (volume, int.from_bytes(file_id, "little")), parent_identity
    except _manifest.ManifestError as exc:
        raise MaterializationError("Binding path could not be safely pinned.", code="binding_invalid") from exc


@contextlib.contextmanager
def _windows_open_relative_for_delete(parent_handle: int, name: str, *, directory: bool | None):
    """Open one child without following reparse points and with DELETE access.

    FILE_SHARE_DELETE is deliberately omitted. If another opener prevents this
    restrictive handle, cleanup fails closed rather than racing a rename.
    """
    if os.name != "nt":
        raise MaterializationError("Windows relative delete open is unavailable.", code="safe_handle_unavailable")
    if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\x00", ":")):
        raise MaterializationError("Unsafe directory entry during cleanup.", code="cleanup_invalid_name")
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusOrPointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    encoded_length = len(name.encode("utf-16-le"))
    if encoded_length > 65532:
        raise MaterializationError("Directory entry name is too long.", code="cleanup_invalid_name")
    name_buffer = ctypes.create_unicode_buffer(name)
    name_descriptor = UnicodeString(encoded_length, encoded_length + 2, ctypes.cast(name_buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent_handle, ctypes.pointer(name_descriptor),
                                  0x40, None, None)
    io, handle = IoStatusBlock(), wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll")
    create = ntdll.NtCreateFile
    create.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(ObjectAttributes),
                       ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
                       wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    create.restype = ctypes.c_long
    # GENERIC_READ | DELETE | FILE_READ_ATTRIBUTES | SYNCHRONIZE; share read/write,
    # but not delete, so this entry cannot be renamed/replaced while disposition is set.
    status = create(ctypes.byref(handle), 0x80110080, ctypes.byref(attributes), ctypes.byref(io),
                    None, 0, 3, 1, 0x00200000 | 0x20 | (0x1 if directory is True else 0x40 if directory is False else 0),
                    None, 0)
    if status < 0:
        raise MaterializationError("A directory entry could not be safely opened.", code="cleanup_open_failed") from OSError(
            f"NtCreateFile failed with NTSTATUS 0x{status & 0xffffffff:08x}"
        )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        identity, info = _manifest._windows_handle_info(handle.value)
        if info["attributes"] & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise MaterializationError("Reparse points are preserved during cleanup.", code="cleanup_reparse")
        if info["delete_pending"]:
            raise MaterializationError("Delete-pending entry is ambiguous.", code="cleanup_delete_pending")
        if directory is not None and info["directory"] != directory:
            raise MaterializationError("Entry type changed during cleanup.", code="cleanup_type_changed")
        yield handle.value, identity, info
    finally:
        kernel.CloseHandle(handle)


def _windows_mark_delete(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    class FileDispositionInformation(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    value = FileDispositionInformation(1)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    set_info = kernel.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    set_info.restype = wintypes.BOOL
    if not set_info(handle, 4, ctypes.byref(value), ctypes.sizeof(value)):
        raise MaterializationError("Delete disposition could not be safely set.", code="cleanup_disposition_failed") from ctypes.WinError(
            ctypes.get_last_error()
        )


def _windows_cleanup_tree_handle(root_handle: int) -> None:
    """Preflight, then post-order delete using only held directory-relative handles."""
    plan: list[tuple[tuple[str, ...], tuple[int, bytes], bool]] = []
    seen_directories: set[tuple[int, bytes]] = set()
    directory_identities: dict[tuple[str, ...], tuple[int, bytes]] = {}

    def inspect(directory_handle: int, components: tuple[str, ...], depth: int) -> None:
        if depth > getattr(_manifest, "_MAX_DEPTH", 128):
            raise MaterializationError("Cleanup depth limit exceeded.", code="cleanup_depth_limit")
        identity, info = _manifest._windows_handle_info(directory_handle)
        if not info["directory"] or info["delete_pending"] or info["attributes"] & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise MaterializationError("Unsafe directory encountered during cleanup.", code="cleanup_unsafe_directory")
        if identity in seen_directories:
            raise MaterializationError("Directory identity alias encountered during cleanup.", code="cleanup_directory_alias")
        seen_directories.add(identity)
        directory_identities[components] = identity
        before = _manifest._windows_enumerate_directory(directory_handle)
        for name in before:
            if (not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\x00", ":"))):
                raise MaterializationError("Unsafe directory name encountered during cleanup.", code="cleanup_invalid_name")
            with _manifest._windows_open_relative(directory_handle, name, directory=None) as child:
                child_id, child_info = _manifest._windows_handle_info(child)
                if child_info["attributes"] & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                    raise MaterializationError("Reparse points are preserved during cleanup.", code="cleanup_reparse")
                if child_info["delete_pending"]:
                    raise MaterializationError("Delete-pending entry is ambiguous.", code="cleanup_delete_pending")
                child_parts = components + (name,)
                if child_info["directory"]:
                    inspect(child, child_parts, depth + 1)
                    plan.append((child_parts, child_id, True))
                else:
                    if child_info["attributes"] & 0x40:
                        raise MaterializationError("Device entry is preserved during cleanup.", code="cleanup_device")
                    if child_info["links"] != 1:
                        raise MaterializationError("Hard-linked file is preserved during cleanup.", code="cleanup_hard_link")
                    # Delete disposition applies to this verified file object;
                    # NTFS streams share its identity and are removed with it.
                    plan.append((child_parts, child_id, False))
        after = _manifest._windows_enumerate_directory(directory_handle)
        if after != before:
            raise MaterializationError("Directory changed during cleanup preflight.", code="cleanup_directory_changed")

    inspect(root_handle, (), 0)

    # No object is removed until the complete tree has passed preflight.
    import contextlib
    for components, expected_id, is_directory in plan:
        with contextlib.ExitStack() as stack:
            parent = root_handle
            for index, component in enumerate(components[:-1], start=1):
                parent, parent_id, _ = stack.enter_context(
                    _windows_open_relative_for_delete(parent, component, directory=True)
                )
                if parent_id != directory_identities.get(components[:index]):
                    raise MaterializationError(
                        "Intermediate directory identity changed after preflight.", code="cleanup_identity_changed"
                    )
            target, identity, info = stack.enter_context(
                _windows_open_relative_for_delete(parent, components[-1], directory=is_directory)
            )
            if identity != expected_id or info["links"] != 1:
                raise MaterializationError("Entry identity/link count changed after preflight.", code="cleanup_identity_changed")
            if is_directory and _manifest._windows_enumerate_directory(target):
                raise MaterializationError("Directory gained entries during cleanup.", code="cleanup_directory_changed")
            _windows_mark_delete(target)

    if _manifest._windows_enumerate_directory(root_handle):
        raise MaterializationError("Root gained entries during cleanup.", code="cleanup_directory_changed")

@dataclass
class _TemporaryTree:
    path: Path
    parent_identity: tuple[int, int]
    root_identity: tuple[int, int]
    parent_handle: int | None = None
    root_handle: int | None = None
    _stack: object | None = None

    def close_root(self) -> None:
        if self.root_handle is not None:
            from ctypes import wintypes
            close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            if not close_handle(self.root_handle):
                raise MaterializationError(
                    "Scratch root handle could not be closed.", code="safe_handle_close_failed"
                ) from ctypes.WinError(ctypes.get_last_error())
            self.root_handle = None

    def close(self) -> None:
        self.close_root()
        if self._stack is not None:
            self._stack.close()
            self._stack = None


def _create_temporary_tree(parent: Path, *, expected_parent_identity: tuple[int, int]) -> _TemporaryTree:
    """Create scratch relative to a pinned authorized parent, never via a path race."""
    if os.name != "nt":
        if SafeSnapshotMaterializer._directory_identity(parent) != expected_parent_identity:
            raise MaterializationError("Temporary parent identity changed.", code="parent_identity_changed")
        path = Path(tempfile.mkdtemp(prefix=".self-evolution-materializer-", dir=parent))
        identity = SafeSnapshotMaterializer._directory_identity(path)
        return _TemporaryTree(path, expected_parent_identity, identity)

    try:
        stack = contextlib.ExitStack()
        raw_parent = os.fspath(parent)
        if not parent.is_absolute() or not re.match(r"^[A-Za-z]:\\", raw_parent):
            raise MaterializationError("Authorized output parent must be a local drive path.", code="unsafe_parent_path")
        if any(part in (".", "..") or part.endswith((".", " ")) or
               any(char in part for char in ("/", "\\", "\x00", ":"))
               for part in parent.parts[1:]):
            raise MaterializationError("Authorized output parent contains an alias component.", code="unsafe_parent_path")
        parent_handle, parent_id = stack.enter_context(_manifest._windows_open_volume_root(parent.drive))
        components = parent.parts[1:]
        for index, component in enumerate(components):
            if component in ("\\", "/"):
                continue
            access = 0x00100081 | (0x00000004 if index == len(components) - 1 else 0)
            parent_handle, info = stack.enter_context(_windows_nt_open_relative(
                parent_handle, component, access=access, share=0x3,
                disposition=1, options=0x1,
            ))
            if not info["directory"] or info["delete_pending"]:
                raise MaterializationError("Authorized output parent is not stable.", code="invalid_directory")
            parent_id = _manifest._windows_handle_identity(parent_handle)
        actual_parent = (int(parent_id[0]), int.from_bytes(parent_id[1], "little"))
        if actual_parent != expected_parent_identity:
            stack.close()
            raise MaterializationError("Temporary parent identity changed.", code="parent_identity_changed")
        # FILE_CREATE (not OPEN_IF) prevents accepting a pre-planted name. The
        # retained root handle omits FILE_SHARE_DELETE, pinning this entry until
        # cleanup marks this exact object for deletion.
        name = f".self-evolution-materializer-{uuid.uuid4().hex}"
        with _windows_nt_open_relative(
            parent_handle, name, access=0x00010081 | 0x00010000 | 0x00100000, share=0x3,
            disposition=2, options=0x1,
        ) as (created, info):
            if not info["directory"] or info["delete_pending"]:
                raise MaterializationError("Scratch root is not a safe directory.", code="unsafe_temp_root")
            created_identity = _manifest._windows_handle_identity(created)
            root_identity = (int(created_identity[0]), int.from_bytes(created_identity[1], "little"))
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            duplicate = kernel.DuplicateHandle
            duplicate.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
            duplicate.restype = ctypes.c_int
            process = kernel.GetCurrentProcess()
            retained = ctypes.c_void_p()
            if not duplicate(process, created, process, ctypes.byref(retained), 0, False, 0x2):
                raise MaterializationError("Scratch root handle could not be retained.", code="safe_handle_unavailable") from ctypes.WinError(ctypes.get_last_error())
        return _TemporaryTree(parent / name, actual_parent, root_identity,
                              parent_handle=int(parent_handle), root_handle=int(retained.value), _stack=stack)
    except MaterializationError:
        if "stack" in locals():
            stack.close()
        raise
    except Exception as exc:
        if "stack" in locals():
            stack.close()
        raise MaterializationError("Handle-relative scratch creation failed closed.", code="safe_create_failed") from exc


def _cleanup_temporary_tree(
    temp_root: Path, *, expected_parent_identity: tuple[int, int],
    expected_root_identity: tuple[int, int], parent_handle: int | None = None,
    root_handle: int | None = None,
) -> None:
    """Delete only the pinned Windows scratch object, never path-traversing its contents."""
    if os.name != "nt":
        if SafeSnapshotMaterializer._directory_identity(temp_root.parent) != expected_parent_identity:
            raise MaterializationError("Temporary parent identity changed; preserving scratch.", code="parent_identity_changed")
        if getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            shutil.rmtree(temp_root)
            return
        raise MaterializationError("Race-safe cleanup is unavailable.", code="unsafe_cleanup_unavailable")

    try:
        if parent_handle is None:
            # Compatibility path for direct cleanup callers: bind the complete
            # trusted ancestor chain now and retain it until disposition.
            with contextlib.ExitStack() as stack:
                _bound, pinned_parent, parent_id, _chain = stack.enter_context(
                    _manifest._windows_open_trusted_root(temp_root.parent)
                )
                if (int(parent_id[0]), int.from_bytes(parent_id[1], "little")) != expected_parent_identity:
                    raise MaterializationError("Temporary parent identity changed; preserving scratch.", code="parent_identity_changed")
                with _windows_open_relative_for_delete(pinned_parent, temp_root.name, directory=True) as (opened, root_id, root_info):
                    if (int(root_id[0]), int.from_bytes(root_id[1], "little")) != expected_root_identity:
                        raise MaterializationError("Temporary root identity changed; preserving replacement.", code="cleanup_root_identity_changed")
                    _windows_cleanup_tree_handle(opened)
                    _windows_mark_delete(opened)
        else:
            parent_id, _ = _manifest._windows_handle_info(parent_handle)
            if (int(parent_id[0]), int.from_bytes(parent_id[1], "little")) != expected_parent_identity:
                raise MaterializationError("Temporary parent identity changed; preserving scratch.", code="parent_identity_changed")
            if root_handle is not None:
                root_id, root_info = _manifest._windows_handle_info(root_handle)
                if (int(root_id[0]), int.from_bytes(root_id[1], "little")) != expected_root_identity or not root_info["directory"]:
                    raise MaterializationError("Temporary root identity changed; preserving replacement.", code="cleanup_root_identity_changed")
                _windows_cleanup_tree_handle(root_handle)
                _windows_mark_delete(root_handle)
            else:
                with _windows_open_relative_for_delete(parent_handle, temp_root.name, directory=True) as (opened, root_id, root_info):
                    if (int(root_id[0]), int.from_bytes(root_id[1], "little")) != expected_root_identity:
                        raise MaterializationError("Temporary root identity changed; preserving replacement.", code="cleanup_root_identity_changed")
                    _windows_cleanup_tree_handle(opened)
                    _windows_mark_delete(opened)
    except MaterializationError:
        raise
    except _manifest.ManifestError as exc:
        detail = str(exc)
        code = "cleanup_reparse" if "reparse" in detail or "link_or_reparse" in detail else "temporary_cleanup_failed"
        raise MaterializationError("Manifest handle traversal refused cleanup; preserve remaining scratch.", code=code) from exc
    except Exception as exc:
        raise MaterializationError("Handle-relative cleanup failed closed; preserve remaining scratch.", code="temporary_cleanup_failed") from exc


def _publish_directory_noreplace(
    source: Path,
    destination: Path,
    *,
    expected_parent_identity: tuple[int, int] | None = None,
    expected_source_identity: tuple[int, int] | None = None,
    pinned_parent_handle: int | None = None,
) -> None:
    """Atomically publish a directory without replacing an existing target.

    Windows uses a source directory handle and a pinned destination-parent
    handle with native FileRenameInformation.RootDirectory. Linux uses renameat2 with
    RENAME_NOREPLACE. Refuse where neither handle-relative/no-replace
    contract is available.
    """
    if os.name == "nt":
        try:
            if pinned_parent_handle is None:
                parent_context = _WindowsDirectoryHandle.open(
                    destination.parent, desired_access=(
                        _WindowsDirectoryHandle._GENERIC_READ_ATTRIBUTES
                        | _WindowsDirectoryHandle._FILE_TRAVERSE
                        | _WindowsDirectoryHandle._FILE_ADD_SUBDIRECTORY
                    )
                )
            else:
                parent_id, parent_info = _manifest._windows_handle_info(pinned_parent_handle)
                actual_id = (int(parent_id[0]), int.from_bytes(parent_id[1], "little"))
                if not parent_info["directory"] or parent_info["delete_pending"]:
                    raise MaterializationError("Pinned output parent is no longer usable.", code="parent_identity_changed")
                parent_context = _WindowsDirectoryHandle(
                    pinned_parent_handle, actual_id, ctypes.WinDLL("kernel32", use_last_error=True), owns_handle=False
                )
            with parent_context as parent_handle:
                if expected_parent_identity is not None and parent_handle.identity != expected_parent_identity:
                    raise MaterializationError(
                        "Destination parent identity changed during materialization.", code="parent_identity_changed"
                    )
                with _WindowsDirectoryHandle.open(
                    source, desired_access=(
                        _WindowsDirectoryHandle._DELETE | _WindowsDirectoryHandle._GENERIC_READ_ATTRIBUTES
                        | _WindowsDirectoryHandle._SYNCHRONIZE
                    )
                ) as source_handle:
                    if expected_source_identity is not None and source_handle.identity != expected_source_identity:
                        raise MaterializationError(
                            "Publication source identity changed.", code="source_identity_changed"
                        )
                    parent_handle.rename_handle_noreplace(source_handle, destination.name)
        except MaterializationError:
            raise
        except OSError as exc:
            raise MaterializationError("Handle-relative directory publication failed.", code="safe_publish_failed") from exc
        return

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise MaterializationError(
                "Atomic no-replace directory publication is unavailable.",
                code="safe_publish_unavailable",
            )
        renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
        if result == 0:
            return
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise MaterializationError(
                "Destination appeared during materialization.", code="destination_race"
            )
        if error in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise MaterializationError(
                "Atomic no-replace directory publication is unavailable.",
                code="safe_publish_unavailable",
            )
        raise OSError(error, os.strerror(error), str(destination))

    raise MaterializationError(
        "Atomic no-replace directory publication is unavailable.",
        code="safe_publish_unavailable",
    )


class _ObjectReader:
    def __init__(self, owner: SafeSnapshotMaterializer, git_dir: Path, cwd: Path,
                 hooks: Path, attrs: Path) -> None:
        self.owner = owner
        command = [owner.git_executable, "-c", f"core.hooksPath={hooks}", "-c", f"core.attributesFile={attrs}",
                   "-c", "core.fsmonitor=false", "-c", "core.quotePath=false", f"--git-dir={git_dir}", "cat-file", "--batch"]
        try:
            self.proc = subprocess.Popen(command, cwd=cwd, env=owner._safe_environment(),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, bufsize=0)
        except OSError as exc:
            raise MaterializationError("Trusted object reader could not start.", code="git_plumbing_failed") from exc
        self._timeout = threading.Timer(owner.timeout_seconds, self._kill_if_running)
        self._timeout.daemon = True
        self._timeout.start()
        self.object_count = 0
        # Set from the first requested object and enforce one format per stream.
        self.hash_name: str | None = None

    def read_object(self, oid: str, expected_type: str, limit: int) -> bytes:
        self.owner._validate_oid(oid)
        assert self.proc.stdin is not None and self.proc.stdout is not None
        try:
            self.proc.stdin.write(oid.encode("ascii") + b"\n")
            self.proc.stdin.flush()
            header = self._read_line(self.proc.stdout, _MAX_HEADER)
            parts = header[:-1].split(b" ")
            if len(parts) != 3 or parts[0].decode("ascii").lower() != oid.lower() or parts[1].decode("ascii") != expected_type:
                raise MaterializationError("Requested Git object is missing or has the wrong type.", code="object_type_mismatch")
            if not parts[2].isdigit():
                raise MaterializationError("Git object size header is invalid.", code="invalid_object")
            size = int(parts[2])
            if size > limit:
                raise MaterializationError("Git object exceeds the configured size limit.", code="blob_too_large" if expected_type == "blob" else "object_too_large")
            data = self._read_exact(self.proc.stdout, size)
            if self._read_exact(self.proc.stdout, 1) != b"\n":
                raise MaterializationError("Git object framing is invalid.", code="invalid_object")
            hash_name = "sha1" if len(oid) == 40 else "sha256"
            canonical = expected_type.encode("ascii") + b" " + str(size).encode("ascii") + b"\0" + data
            if hashlib.new(hash_name, canonical).hexdigest() != oid.lower():
                raise MaterializationError("Git object content does not match its object id.", code="object_hash_mismatch")
            if self.hash_name is None:
                self.hash_name = hash_name
            elif self.hash_name != hash_name:
                raise MaterializationError("Mixed Git object formats are not allowed.", code="invalid_object_id")
            self.object_count += 1
            return data
        except (BrokenPipeError, OSError, UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, MaterializationError):
                raise
            raise MaterializationError("Git object stream failed closed.", code="git_plumbing_failed") from exc

    @staticmethod
    def _read_line(stream: BinaryIO, limit: int) -> bytes:
        result = bytearray()
        while len(result) < limit:
            byte = stream.read(1)
            if not byte:
                raise MaterializationError("Git object stream ended unexpectedly.", code="git_plumbing_failed")
            result += byte
            if byte == b"\n":
                return bytes(result)
        raise MaterializationError("Git object header exceeds its limit.", code="invalid_object")

    @staticmethod
    def _read_exact(stream: BinaryIO, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = stream.read(min(1024 * 1024, length - len(chunks)))
            if not chunk:
                raise MaterializationError("Git object stream ended unexpectedly.", code="git_plumbing_failed")
            chunks.extend(chunk)
        return bytes(chunks)

    def _kill_if_running(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass

    def close(self) -> None:
        self._timeout.cancel()
        proc = self.proc
        if proc.poll() is None:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for stream in (proc.stdin, proc.stdout):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
