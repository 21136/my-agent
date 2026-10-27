"""Validated production context and process-local read-only guard.

This is logical separation for trusted application code. It is not an OS sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

MAX_FRAME_BYTES = 16 * 1024
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_RUNTIME_FILE_BYTES = 512 * 1024 * 1024
MAX_RUNTIME_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_RUNTIME_FILES = 60_000
PROTOCOL = "my-agent-managed-readonly-v1"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_AUDIT_HOOK_INSTALLED = False


class ManagedReadonlyError(ValueError):
    """A production read-only context failed closed validation."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManagedReadonlyError("managed read-only record has duplicate fields")
        result[key] = value
    return result


def _exact(value: Any, keys: set[str], label: str) -> None:
    if type(value) is not dict or set(value) != keys:
        raise ManagedReadonlyError(f"{label} fields rejected")


def _parse_canonical_record(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_RECORD_BYTES:
            raise ManagedReadonlyError("managed read-only record file rejected")
        raw = path.read_bytes()
    except OSError as exc:
        raise ManagedReadonlyError("managed read-only record unavailable") from exc
    if not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw or len(raw) > MAX_RECORD_BYTES:
        raise ManagedReadonlyError("managed read-only record frame rejected")
    try:
        record = json.loads(raw[:-1].decode("utf-8", "strict"), object_pairs_hook=_reject_duplicate_keys,
                            parse_constant=lambda _: (_ for _ in ()).throw(ManagedReadonlyError("invalid record constant")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManagedReadonlyError("managed read-only record JSON rejected") from exc
    if type(record) is not dict or _canonical(record) + b"\n" != raw:
        raise ManagedReadonlyError("managed read-only record is not canonical")
    return record


def parse_managed_readonly_frame(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_FRAME_BYTES:
        raise ValueError("managed read-only bootstrap frame exceeds its bound")
    if not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
        raise ValueError("managed read-only bootstrap must be one newline frame")
    try:
        frame = json.loads(raw[:-1].decode("utf-8", "strict"), object_pairs_hook=_reject_duplicate_keys,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("managed read-only bootstrap JSON rejected") from exc
    keys = {"protocol", "namespace_root", "binding_digest", "runtime_digest", "nonce"}
    if type(frame) is not dict or set(frame) != keys or frame.get("protocol") != PROTOCOL:
        raise ValueError("managed read-only bootstrap fields rejected")
    if _canonical(frame) + b"\n" != raw:
        raise ValueError("managed read-only bootstrap is not canonical")
    for key in ("binding_digest", "runtime_digest"):
        if type(frame[key]) is not str or not HASH_RE.fullmatch(frame[key]):
            raise ValueError("managed read-only digest rejected")
    if type(frame["nonce"]) is not str or not UUID_RE.fullmatch(frame["nonce"]):
        raise ValueError("managed read-only nonce rejected")
    root = frame["namespace_root"]
    if type(root) is not str or not root or not Path(root).is_absolute() or str(Path(root)) != root:
        raise ValueError("managed read-only namespace root rejected")
    return frame


def _canonical_path(raw: Any, label: str, *, directory: bool = True) -> Path:
    if type(raw) is not str or not raw or not Path(raw).is_absolute() or str(Path(raw)) != raw:
        raise ManagedReadonlyError(f"{label} path rejected")
    path = Path(raw)
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as exc:
        raise ManagedReadonlyError(f"{label} path unavailable") from exc
    if path != resolved or path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ManagedReadonlyError(f"{label} path alias rejected")
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ManagedReadonlyError(f"{label} is not a directory")
    return path


def _is_empty_directory(path: Path) -> None:
    if not path.is_dir() or path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ManagedReadonlyError("managed read-only store root rejected")
    try:
        next(path.iterdir())
    except StopIteration:
        return
    except OSError as exc:
        raise ManagedReadonlyError("managed read-only store root unavailable") from exc
    raise ManagedReadonlyError("managed read-only namespace is not empty")


def _directory_names(path: Path) -> list[str]:
    try:
        _canonical_path(str(path), "namespace directory")
        return sorted(item.name for item in path.iterdir())
    except OSError as exc:
        raise ManagedReadonlyError("managed read-only namespace layout unavailable") from exc


def validate_fixed_namespace_layout(namespace_root: Path, release_id: str) -> None:
    root = namespace_root
    expected_tree = {
        ".": ["data", "host", "launcher", "releases", "state"],
        "state": ["managed-namespace.json", "production-binding.json", "production-context-intent.json", "production-context-receipt.json"],
        "launcher": ["runtime-intent.json", "runtime-receipt.json", "runtime-registration.json"],
        "releases": [release_id],
        "host": ["profile", "state", "workspace"],
        "data": ["store"],
    }
    for relative, expected in expected_tree.items():
        directory = root if relative == "." else root / relative
        if _directory_names(directory) != expected:
            raise ManagedReadonlyError("managed production namespace contains unknown or missing entries")


def validate_runtime_registration_metadata(registration: dict[str, Any]) -> None:
    runtimes = registration.get("runtimes")
    if type(runtimes) is not dict or set(runtimes) != {"node", "electron", "python", "pythonBase"}:
        raise ManagedReadonlyError("runtime registration set rejected")
    expected = {
        "node": ("single_executable_v1", "node.exe"),
        "electron": ("complete_tree_v1", "electron.exe"),
        "python": ("complete_tree_v1", "Scripts/python.exe"),
        "pythonBase": ("complete_tree_v1", "python.exe"),
    }
    total_count = 0
    total_bytes = 0
    for name in ("node", "electron", "python", "pythonBase"):
        record = runtimes[name]
        _exact(record, {"root", "entry", "policy", "files", "directories"}, f"runtime {name}")
        root = record["root"]
        if type(root) is not str or not Path(root).is_absolute() or str(Path(root)) != root:
            raise ManagedReadonlyError("runtime root metadata rejected")
        policy, expected_entry = expected[name]
        if record["policy"] != policy or record["entry"] != expected_entry:
            raise ManagedReadonlyError("runtime policy or executable entry rejected")
        if type(record["files"]) is not list or not record["files"] or type(record["directories"]) is not list:
            raise ManagedReadonlyError("runtime inventory metadata rejected")
        previous = ""
        file_names: list[str] = []
        for item in record["files"]:
            _exact(item, {"path", "sha256", "byteLength"}, "runtime file metadata")
            relative = item["path"]
            length = item["byteLength"]
            if (type(relative) is not str or not relative or not relative.isascii() or relative.startswith(("/", "\\")) or "\\" in relative
                    or any(part in {"", ".", ".."} for part in relative.split("/")) or relative <= previous
                    or type(item["sha256"]) is not str or not HASH_RE.fullmatch(item["sha256"])
                    or type(length) is not int or not 0 <= length <= MAX_RUNTIME_FILE_BYTES):
                raise ManagedReadonlyError("runtime file metadata invalid")
            previous = relative
            file_names.append(relative)
            total_count += 1
            total_bytes += length
        directories = record["directories"]
        if directories != sorted(set(directories)):
            raise ManagedReadonlyError("runtime directory metadata is not sorted and unique")
        for relative in directories:
            if (type(relative) is not str or not relative or not relative.isascii() or relative.startswith(("/", "\\")) or "\\" in relative
                    or any(part in {"", ".", ".."} for part in relative.split("/"))):
                raise ManagedReadonlyError("runtime directory metadata invalid")
        if record["entry"] not in file_names:
            raise ManagedReadonlyError("runtime entry missing from inventory")
        if policy == "single_executable_v1" and (len(file_names) != 1 or directories):
            raise ManagedReadonlyError("standalone runtime metadata invalid")
        directory_set = set(directories)
        for relative in file_names:
            parts = relative.split("/")[:-1]
            for index in range(1, len(parts) + 1):
                if "/".join(parts[:index]) not in directory_set:
                    raise ManagedReadonlyError("runtime inventory omits a file parent directory")
        if total_count > MAX_RUNTIME_FILES or total_bytes > MAX_RUNTIME_TOTAL_BYTES:
            raise ManagedReadonlyError("runtime registration aggregate limits exceeded")


def validate_runtime_record_chain(
    registration: dict[str, Any], intent: dict[str, Any], receipt: dict[str, Any], binding_digest: str,
    *, installation_id: str | None = None, release_id: str | None = None,
) -> bool:
    """Verify canonical digest links across the runtime registration and its durable bookends."""
    if type(registration) is not dict or type(intent) is not dict or type(receipt) is not dict:
        raise ManagedReadonlyError("runtime registration chain rejected")
    _exact(registration, {"format", "mode", "installationId", "releaseId", "bindingDigest", "runtimes", "readyForLaunch", "runtimeDigest"}, "runtime registration")
    if registration["format"] != 1 or registration["mode"] != "managed_runtime_registration_v1" or registration["readyForLaunch"] is not False:
        raise ManagedReadonlyError("runtime registration mode rejected")
    body = {key: value for key, value in registration.items() if key != "runtimeDigest"}
    if registration["runtimeDigest"] != _digest(body) or registration["bindingDigest"] != binding_digest:
        raise ManagedReadonlyError("runtime registration digest binding rejected")
    if installation_id is not None and registration["installationId"] != installation_id:
        raise ManagedReadonlyError("runtime registration installation mismatch")
    if release_id is not None and registration["releaseId"] != release_id:
        raise ManagedReadonlyError("runtime registration release mismatch")
    validate_runtime_registration_metadata(registration)

    # Intent and receipt schemas are frozen with the launcher worker.
    if set(intent) != {"format", "mode", "installationId", "releaseId", "bindingDigest", "runtimeDigest", "state", "readyForLaunch", "intentDigest"}:
        raise ManagedReadonlyError("runtime intent fields rejected")
    intent_body = {key: value for key, value in intent.items() if key != "intentDigest"}
    if (intent.get("format") != 1 or intent.get("mode") != "managed_runtime_intent_v1"
            or intent.get("state") != "registration_started" or intent.get("readyForLaunch") is not False
            or intent.get("intentDigest") != _digest(intent_body)):
        raise ManagedReadonlyError("runtime intent digest rejected")
    if (intent.get("installationId"), intent.get("releaseId"), intent.get("bindingDigest"), intent.get("runtimeDigest")) != (
        registration["installationId"], registration["releaseId"], binding_digest, registration["runtimeDigest"]
    ):
        raise ManagedReadonlyError("runtime intent identity mismatch")
    if set(receipt) != {"format", "mode", "installationId", "releaseId", "bindingDigest", "runtimeDigest", "registrationDigest", "readyForLaunch", "receiptDigest"}:
        raise ManagedReadonlyError("runtime receipt fields rejected")
    receipt_body = {key: value for key, value in receipt.items() if key != "receiptDigest"}
    if (receipt.get("format") != 1 or receipt.get("mode") != "managed_runtime_receipt_v1"
            or receipt.get("readyForLaunch") is not False or receipt.get("receiptDigest") != _digest(receipt_body)):
        raise ManagedReadonlyError("runtime receipt digest rejected")
    if (receipt.get("installationId"), receipt.get("releaseId"), receipt.get("bindingDigest"), receipt.get("runtimeDigest")) != (
        registration["installationId"], registration["releaseId"], binding_digest, registration["runtimeDigest"]
    ):
        raise ManagedReadonlyError("runtime receipt identity mismatch")
    if receipt.get("registrationDigest") != _digest(registration):
        raise ManagedReadonlyError("runtime receipt registration digest mismatch")
    return True


@dataclass(frozen=True, slots=True)
class ManagedReadonlyContext:
    namespace_root: Path
    code_root: Path
    evolve_root: Path
    host_state_root: Path
    host_profile_root: Path
    workspace_root: Path
    data_root: Path
    installation_id: str
    release_id: str
    binding_digest: str
    runtime_digest: str
    nonce: str = ""
    _record_paths: tuple[Path, ...] = ()
    _source_probe: bool = False

    @classmethod
    def _from_validated_records(cls, *, namespace_root: Path, binding: dict[str, Any], runtime_digest: str,
                                nonce: str = "", code_root_override: Path | None = None,
                                record_paths: tuple[Path, ...] = (), source_probe: bool = False) -> "ManagedReadonlyContext":
        paths = binding["paths"]
        code_root = code_root_override or Path(paths["code"])
        return cls(namespace_root, code_root, code_root / "evolve", Path(paths["hostState"]),
                   Path(paths["hostProfile"]), Path(paths["hostWorkspace"]), Path(paths["data"]),
                   binding["installationId"], binding["release"]["releaseId"], binding["bindingDigest"], runtime_digest,
                   nonce, record_paths, source_probe)

    def revalidate(self) -> None:
        if self._source_probe:
            for folder in (self.host_state_root, self.host_profile_root, self.workspace_root, self.data_root):
                _is_empty_directory(folder)
            return
        marker = _parse_canonical_record(self.namespace_root / "state" / "managed-namespace.json")
        binding = _parse_canonical_record(self.namespace_root / "state" / "production-binding.json")
        validate_production_binding(self.namespace_root, marker, binding, self.binding_digest)
        registration = _parse_canonical_record(self.namespace_root / "launcher" / "runtime-registration.json")
        intent = _parse_canonical_record(self.namespace_root / "launcher" / "runtime-intent.json")
        receipt = _parse_canonical_record(self.namespace_root / "launcher" / "runtime-receipt.json")
        validate_runtime_record_chain(registration, intent, receipt, self.binding_digest,
                                      installation_id=binding["installationId"], release_id=binding["release"]["releaseId"])
        context_intent = _parse_canonical_record(self.namespace_root / "state" / "production-context-intent.json")
        context_receipt = _parse_canonical_record(self.namespace_root / "state" / "production-context-receipt.json")
        validate_production_context_record_chain(marker, binding, context_intent, context_receipt)
        if registration["runtimeDigest"] != self.runtime_digest:
            raise ManagedReadonlyError("runtime registration changed")
        for folder in (self.host_state_root, self.host_profile_root, self.workspace_root, self.data_root):
            _is_empty_directory(folder)

    def registered_read_roots(self, runtime_roots: Iterable[Path] = ()) -> tuple[Path, ...]:
        if self._source_probe:
            # A source checkout contains user data and credentials beside code.
            # Permit only importable source trees, never the checkout as a whole.
            roots = (self.namespace_root, self.code_root / "agent-core", self.code_root / "evolve", *tuple(runtime_roots))
        else:
            roots = (self.namespace_root, *tuple(runtime_roots))
        return tuple(dict.fromkeys(Path(root).resolve(strict=True) for root in roots))


def validate_production_binding(namespace_root: Path, marker: dict[str, Any], binding: dict[str, Any], expected_digest: str,
                                *, source_probe_code_root: Path | None = None) -> None:
    _exact(marker, {"format", "mode", "stage", "data_policy", "security_policy", "credentials_policy", "installation_id", "root", "source_roots", "paths"}, "namespace marker")
    if marker["format"] != 1 or marker["mode"] != "managed_project_local_v1" or marker["stage"] != "prepared_empty":
        raise ManagedReadonlyError("production namespace mode rejected")
    if marker["data_policy"] != "empty_no_migration" or marker["security_policy"] != "logical_separation_only" or marker["credentials_policy"] != "forbidden":
        raise ManagedReadonlyError("production namespace policy rejected")
    root = _canonical_path(str(namespace_root), "namespace")
    _exact(marker["source_roots"], {"stable", "sandbox"}, "namespace source roots")
    if marker["root"] != str(root) or marker["source_roots"].get("sandbox") is None:
        raise ManagedReadonlyError("production namespace identity mismatch")
    for side, raw in marker["source_roots"].items():
        _canonical_path(raw, f"namespace source root {side}")
    if root != Path(marker["source_roots"]["sandbox"]) / ".managed-runtime" / marker["installation_id"]:
        raise ManagedReadonlyError("production namespace location rejected")
    expected_marker_paths = {"launcher": str(root / "launcher"), "state": str(root / "state"), "releases": str(root / "releases"),
                             "host_state": str(root / "host/state"), "host_profile": str(root / "host/profile"),
                             "host_workspace": str(root / "host/workspace"), "data": str(root / "data/store")}
    if marker["paths"] != expected_marker_paths:
        raise ManagedReadonlyError("production namespace fixed paths rejected")
    binding_keys = {"format", "mode", "installationId", "namespace", "source", "release", "paths", "data", "access", "runtimeRegistration", "writerLease", "readyForLaunch", "bindingDigest"}
    _exact(binding, binding_keys, "production binding")
    body = {key: value for key, value in binding.items() if key != "bindingDigest"}
    if binding["format"] != 1 or binding["mode"] != "managed_production_binding_v1" or binding["bindingDigest"] != _digest(body) or binding["bindingDigest"] != expected_digest:
        raise ManagedReadonlyError("production binding digest rejected")
    installation_id = binding["installationId"]
    release = binding["release"]
    _exact(binding["namespace"], {"mode", "root", "digest", "securityPolicy", "credentialsPolicy"}, "binding namespace")
    _exact(binding["source"], {"operationId", "planDigest", "receiptDigest", "snapshotDigest", "mergedHead", "mergedTree", "roots"}, "binding source")
    _exact(binding["release"], {"releaseId", "manifestDigest", "root", "buildReceiptDigest", "toolchainDigest", "buildPolicy"}, "binding release")
    _exact(binding["data"], {"storeId", "schema", "revision", "policy"}, "binding data")
    if not isinstance(installation_id, str) or not UUID_RE.fullmatch(installation_id) or release.get("releaseId") != release.get("manifestDigest"):
        raise ManagedReadonlyError("production binding identity rejected")
    release_id = release["releaseId"]
    if (marker.get("installation_id") != installation_id or not HASH_RE.fullmatch(str(release_id))
            or release.get("buildPolicy") != "configless_snapshot_v2"
            or any(not HASH_RE.fullmatch(str(release[key])) for key in ("buildReceiptDigest", "toolchainDigest"))):
        raise ManagedReadonlyError("production release provenance rejected")
    source = binding["source"]
    if not UUID_RE.fullmatch(str(source["operationId"])) or any(not HASH_RE.fullmatch(str(source[key])) for key in ("planDigest", "receiptDigest", "snapshotDigest")):
        raise ManagedReadonlyError("production source provenance rejected")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", str(source["mergedHead"])) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", str(source["mergedTree"])):
        raise ManagedReadonlyError("production Git identity rejected")
    _exact(source["roots"], {"stable", "sandbox"}, "binding source roots")
    for side in ("stable", "sandbox"):
        record = source["roots"][side]
        _exact(record, {"worktreeRoot", "gitDir", "commonDir", "branch"}, f"{side} source root")
        if record["worktreeRoot"] != marker["source_roots"][side] or type(record["branch"]) is not str or not record["branch"]:
            raise ManagedReadonlyError("production source root identity rejected")
        for field in ("worktreeRoot", "gitDir", "commonDir"):
            _canonical_path(record[field], f"{side} {field}")
        try:
            Path(record["gitDir"]).relative_to(Path(record["commonDir"]))
        except ValueError as exc:
            raise ManagedReadonlyError("production Git directory is outside common directory") from exc
    if binding["source"]["roots"]["stable"]["commonDir"] != binding["source"]["roots"]["sandbox"]["commonDir"]:
        raise ManagedReadonlyError("production source roots do not share Git metadata")
    fixed = {"launcher": str(root / "launcher"), "state": str(root / "state"), "hostState": str(root / "host/state"),
             "hostProfile": str(root / "host/profile"), "hostWorkspace": str(root / "host/workspace"),
             "data": str(root / "data/store"), "code": str(root / "releases" / release_id / "app")}
    if source_probe_code_root is not None:
        fixed["code"] = str(_canonical_path(str(source_probe_code_root), "source probe code root"))
    if binding["paths"] != fixed or release.get("root") != str(root / "releases" / release_id):
        raise ManagedReadonlyError("production binding path identity rejected")
    if binding["namespace"] != {"mode": "managed_project_local_v1", "root": str(root), "digest": _digest(marker),
                                "securityPolicy": "logical_separation_only", "credentialsPolicy": "forbidden"}:
        raise ManagedReadonlyError("production namespace binding mismatch")
    if binding["installationId"] != marker["installation_id"]:
        raise ManagedReadonlyError("production installation identity mismatch")
    if binding["data"] != {"storeId": binding["data"].get("storeId"), "schema": "managed_empty_store_v1", "revision": 0, "policy": "empty_no_migration"}:
        raise ManagedReadonlyError("production data binding rejected")
    if type(binding["data"].get("storeId")) is not str or not UUID_RE.fullmatch(binding["data"]["storeId"]):
        raise ManagedReadonlyError("production data store identity rejected")
    if binding["access"] != "read_only" or binding["runtimeRegistration"] is not None or binding["writerLease"] is not None or binding["readyForLaunch"] is not False:
        raise ManagedReadonlyError("production access or readiness rejected")
    for relative in ("launcher", "state", "releases", "host", "host/state", "host/profile", "host/workspace", "data", "data/store", f"releases/{release_id}", f"releases/{release_id}/app"):
        _canonical_path(str(root / relative), f"namespace/{relative}")
    validate_fixed_namespace_layout(root, release_id)
    _is_empty_directory(root / "data/store")
    for relative in ("host/state", "host/profile", "host/workspace"):
        _is_empty_directory(root / relative)


def validate_production_context_record_chain(
    marker: dict[str, Any], binding: dict[str, Any], intent: dict[str, Any], receipt: dict[str, Any]
) -> None:
    _exact(intent, {"format", "mode", "installationId", "operationId", "releaseId", "namespaceDigest", "bindingDigest", "dataStoreId", "state", "readyForLaunch", "intentDigest"}, "production context intent")
    _exact(receipt, {"format", "mode", "installationId", "operationId", "releaseId", "bindingDigest", "namespaceDigest", "manifestDigest", "dataStoreId", "filesDigest", "payloadBytes", "allocationBytes", "readyForLaunch", "receiptDigest"}, "production context receipt")
    intent_body = {key: value for key, value in intent.items() if key != "intentDigest"}
    receipt_body = {key: value for key, value in receipt.items() if key != "receiptDigest"}
    expected_common = (binding["installationId"], binding["source"]["operationId"], binding["release"]["releaseId"], binding["bindingDigest"], _digest(marker), binding["data"]["storeId"])
    actual_intent = (intent["installationId"], intent["operationId"], intent["releaseId"], intent["bindingDigest"], intent["namespaceDigest"], intent["dataStoreId"])
    actual_receipt = (receipt["installationId"], receipt["operationId"], receipt["releaseId"], receipt["bindingDigest"], receipt["namespaceDigest"], receipt["dataStoreId"])
    if (intent["format"] != 1 or intent["mode"] != "managed_production_context_intent_v1" or intent["state"] != "copy_started"
            or intent["readyForLaunch"] is not False or intent["intentDigest"] != _digest(intent_body) or actual_intent != expected_common):
        raise ManagedReadonlyError("production context intent binding rejected")
    if (receipt["format"] != 1 or receipt["mode"] != "managed_production_context_receipt_v1"
            or receipt["manifestDigest"] != binding["release"]["manifestDigest"] or receipt["readyForLaunch"] is not False
            or not HASH_RE.fullmatch(str(receipt["filesDigest"])) or type(receipt["payloadBytes"]) is not int
            or type(receipt["allocationBytes"]) is not int or not 0 <= receipt["payloadBytes"] <= 64 * 1024 * 1024
            or receipt["allocationBytes"] < receipt["payloadBytes"] or receipt["receiptDigest"] != _digest(receipt_body)
            or actual_receipt != expected_common):
        raise ManagedReadonlyError("production context receipt binding rejected")


def _validate_context_records_for_source_probe(
    namespace_root: Path,
    marker: dict[str, Any],
    binding: dict[str, Any],
    registration: dict[str, Any],
    intent: dict[str, Any],
    receipt: dict[str, Any],
    *,
    code_root: Path,
    nonce: str = "12345678-1234-4234-8234-123456789abc",
) -> ManagedReadonlyContext:
    """Validate owned synthetic records for the source-component proof only."""
    validate_production_binding(namespace_root, marker, binding, binding.get("bindingDigest", ""), source_probe_code_root=code_root)
    context_intent = _parse_canonical_record(namespace_root / "state/production-context-intent.json")
    context_receipt = _parse_canonical_record(namespace_root / "state/production-context-receipt.json")
    validate_production_context_record_chain(marker, binding, context_intent, context_receipt)
    validate_runtime_record_chain(registration, intent, receipt, binding["bindingDigest"],
                                  installation_id=binding["installationId"], release_id=binding["release"]["releaseId"])
    if Path(binding["paths"]["code"]) != code_root:
        raise ManagedReadonlyError("source probe code root differs from binding")
    context = ManagedReadonlyContext._from_validated_records(
        namespace_root=namespace_root, binding=binding, runtime_digest=registration["runtimeDigest"],
        nonce=nonce, code_root_override=code_root, source_probe=True
    )
    context.revalidate()
    return context


class ManagedReadonlyAuditGuard:
    _MUTATION_EVENTS = frozenset({"os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.chmod", "os.chown", "os.link", "os.symlink", "os.truncate", "os.utime", "os.fchmod", "os.fchown", "os.ftruncate", "os.futime", "shutil.copyfile", "shutil.copymode", "shutil.copystat"})
    _WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def __init__(self, read_roots: Iterable[Path]) -> None:
        self.read_roots = tuple(Path(root).resolve(strict=True) for root in read_roots)

    def _allowed(self, value: Any) -> bool:
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        try:
            candidate = Path(os.fsdecode(value)).resolve(strict=True)
        except (OSError, ValueError):
            return False
        return any(candidate == root or root in candidate.parents for root in self.read_roots)

    def audit(self, event: str, event_args: tuple[Any, ...]) -> None:
        # sys.addaudithook calls hooks as hook(event, args_tuple).
        args = event_args if isinstance(event_args, tuple) else (event_args,)
        if event == "sqlite3.connect":
            # SQLite opens files in native code without an ordinary open event.
            # Permit only canonical existing registered files through mode=ro;
            # disallow extra URI options, alternate VFS, ambient paths and creation.
            raw = args[0] if args else None
            try:
                uri = urlsplit(raw) if type(raw) is str else None
                if uri is None or uri.scheme != "file" or uri.netloc or uri.query != "mode=ro" or uri.fragment:
                    raise ValueError("readonly SQLite URI required")
                spelling = unquote(uri.path, errors="strict")
                if os.name == "nt" and re.match(r"^/[A-Za-z]:/", spelling):
                    spelling = spelling[1:]
                path = Path(spelling).resolve(strict=True)
                if raw != path.as_uri() + "?mode=ro" or not self._allowed(path) or not path.is_file():
                    raise ValueError("unregistered SQLite path")
            except (ValueError, OSError, UnicodeError):
                raise PermissionError("managed read-only process denied writable or unregistered SQLite access") from None
            return
        if event == "_winapi.CreateFile":
            # The audit event exposes native handle creation, which can request
            # write access without passing through Python's ordinary open event.
            # Fail closed for this API in the read-only process.
            raise PermissionError("managed read-only process denied Windows file handle creation")
        if (event in self._MUTATION_EVENTS or event.startswith(("subprocess.", "os.exec", "os.spawn", "os.posix_spawn"))
                or event in {"os.system", "os.chdir", "os.fchdir", "os.startfile", "os.startfile/2"}):
            raise PermissionError("managed read-only process denied mutation or process execution")
        if event in {"socket.connect", "socket.connect_ex"}:
            address = args[1] if len(args) > 1 else None
            if not isinstance(address, tuple) or not address or address[0] not in {"127.0.0.1", "::1"}:
                raise PermissionError("managed read-only process denied non-loopback network access")
        if event == "open":
            path = args[0] if args else None
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 and type(args[2]) is int else 0
            writing = (isinstance(mode, str) and any(char in mode for char in "wax+")) or bool(flags & self._WRITE_FLAGS)
            if writing:
                if type(path) is int and path in (1, 2):
                    return
                raise PermissionError("managed read-only process denied filesystem write open")
            if type(path) is int:
                if path == 0:
                    return
                raise PermissionError("managed read-only process denied unknown file descriptor read")
            if not self._allowed(path):
                raise PermissionError("managed read-only process denied unregistered filesystem read")
        if event in {"os.listdir", "os.scandir"}:
            path = args[0] if args else None
            if type(path) is int or not self._allowed(path):
                raise PermissionError("managed read-only process denied unregistered directory read")

    def install(self) -> None:
        import sys
        global _AUDIT_HOOK_INSTALLED
        if _AUDIT_HOOK_INSTALLED:
            return
        sys.addaudithook(self.audit)
        _AUDIT_HOOK_INSTALLED = True


def read_roots_for_context(context: ManagedReadonlyContext, runtime_roots: Iterable[Path] = ()) -> tuple[Path, ...]:
    return context.registered_read_roots(runtime_roots)


def verify_runtime_inventory(registration: dict[str, Any]) -> tuple[Path, ...]:
    """Re-hash every explicitly registered runtime file under bounded limits."""
    if type(registration.get("runtimes")) is not dict or set(registration["runtimes"]) != {"node", "electron", "python", "pythonBase"}:
        raise ManagedReadonlyError("registered runtime set rejected")
    count = 0
    total = 0
    actual_tree_entries = 0
    roots: list[Path] = []
    for name in ("node", "electron", "python", "pythonBase"):
        record = registration["runtimes"][name]
        _exact(record, {"root", "entry", "policy", "files", "directories"}, f"runtime {name}")
        root = _canonical_path(record["root"], f"runtime {name} root")
        roots.append(root)
        if record["policy"] not in {"single_executable_v1", "complete_tree_v1"}:
            raise ManagedReadonlyError("registered runtime policy rejected")
        if type(record["files"]) is not list or type(record["directories"]) is not list:
            raise ManagedReadonlyError("registered runtime inventory rejected")
        previous = ""
        expected_files: list[str] = []
        for item in record["files"]:
            _exact(item, {"path", "sha256", "byteLength"}, "runtime file")
            relative = item["path"]
            if type(relative) is not str or not relative or relative.startswith(("/", "\\")) or "\\" in relative or any(part in {"", ".", ".."} for part in relative.split("/")) or relative <= previous:
                raise ManagedReadonlyError("registered runtime file path rejected")
            previous = relative
            expected_files.append(relative)
            byte_length = item["byteLength"]
            if type(byte_length) is not int or byte_length < 0 or byte_length > MAX_RUNTIME_FILE_BYTES or not HASH_RE.fullmatch(str(item["sha256"])):
                raise ManagedReadonlyError("registered runtime file bounds rejected")
            path = root.joinpath(*relative.split("/"))
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != byte_length:
                    raise ManagedReadonlyError("registered runtime file identity changed")
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != item["sha256"]:
                    raise ManagedReadonlyError("registered runtime file digest changed")
            except OSError as exc:
                raise ManagedReadonlyError("registered runtime file unavailable") from exc
            count += 1
            total += byte_length
            if count > MAX_RUNTIME_FILES or total > MAX_RUNTIME_TOTAL_BYTES:
                raise ManagedReadonlyError("registered runtime aggregate bound exceeded")
        if record["policy"] == "single_executable_v1" and (len(record["files"]) != 1 or record["directories"]):
            raise ManagedReadonlyError("standalone runtime inventory rejected")
        if type(record["entry"]) is not str or record["entry"] not in set(expected_files):
            raise ManagedReadonlyError("registered runtime entry rejected")
        if type(record["directories"]) is not list or record["directories"] != sorted(set(record["directories"])):
            raise ManagedReadonlyError("registered runtime directory list rejected")
        for relative in record["directories"]:
            if (type(relative) is not str or not relative or not relative.isascii() or relative.startswith(("/", "\\")) or "\\" in relative
                    or any(part in {"", ".", ".."} for part in relative.split("/"))):
                raise ManagedReadonlyError("registered runtime directory path rejected")
        if record["policy"] == "single_executable_v1":
            if record["entry"] != expected_files[0] or "/" in record["entry"]:
                raise ManagedReadonlyError("standalone runtime entry must be one filename")
            # This policy intentionally registers only the executable. Do not
            # enumerate its installation root: unrelated sibling files and
            # reparse points are outside the registered runtime boundary.
            continue
        actual_files: list[str] = []
        actual_directories: list[str] = []
        pending = [(root, "")]
        while pending:
            directory, relative_parent = pending.pop()
            try:
                with os.scandir(directory) as scan:
                    for child in scan:
                        actual_tree_entries += 1
                        if actual_tree_entries > MAX_RUNTIME_FILES:
                            raise ManagedReadonlyError("registered runtime actual tree entry limit exceeded")
                        relative = f"{relative_parent}/{child.name}" if relative_parent else child.name
                        try:
                            child_path = Path(child.path)
                            info = child_path.lstat()
                        except OSError as exc:
                            raise ManagedReadonlyError("registered runtime tree changed during inventory") from exc
                        if child.is_symlink() or getattr(Path(child.path), "is_junction", lambda: False)():
                            raise ManagedReadonlyError("registered runtime tree contains a link")
                        if stat.S_ISDIR(info.st_mode):
                            if info.st_nlink < 1:
                                raise ManagedReadonlyError("registered runtime directory identity rejected")
                            actual_directories.append(relative)
                            pending.append((child_path, relative))
                        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                            actual_files.append(relative)
                        else:
                            raise ManagedReadonlyError("registered runtime tree contains a special file")
            except OSError as exc:
                raise ManagedReadonlyError("registered runtime tree unavailable") from exc
        actual_files.sort()
        actual_directories.sort()
        if actual_files != expected_files or actual_directories != record["directories"]:
            raise ManagedReadonlyError("registered runtime inventory differs from the complete tree")
    return tuple(roots)
