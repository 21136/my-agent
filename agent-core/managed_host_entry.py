"""Cold production-only entry for a read-only ordinary Python server."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import stat
from pathlib import Path
from typing import Any

MAX_FRAME_BYTES = 16 * 1024
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _safe_relative(value: Any) -> bool:
    if (type(value) is not str or not value or len(value) > 1024 or value.startswith("/")
            or re.search(r'[\\:\x00-\x1f\x7f<>"|?*]', value)):
        return False
    for part in value.split("/"):
        if not part or part in {".", ".."} or part.endswith((".", " ")) or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.IGNORECASE):
            return False
    return True


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate managed read-only bootstrap field")
        result[key] = value
    return result


def parse_managed_readonly_bootstrap(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
        raise ValueError("managed read-only bootstrap frame rejected")
    try:
        frame = json.loads(raw[:-1].decode("utf-8", "strict"), object_pairs_hook=_reject_duplicate_keys,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("managed read-only bootstrap JSON rejected") from exc
    if type(frame) is not dict or set(frame) != {"protocol", "namespace_root", "binding_digest", "runtime_digest", "nonce"}:
        raise ValueError("managed read-only bootstrap fields rejected")
    if frame["protocol"] != "my-agent-managed-readonly-v1" or _canonical(frame) + b"\n" != raw:
        raise ValueError("managed read-only bootstrap protocol rejected")
    if type(frame["namespace_root"]) is not str or not Path(frame["namespace_root"]).is_absolute() or str(Path(frame["namespace_root"])) != frame["namespace_root"]:
        raise ValueError("managed read-only namespace path rejected")
    if any(type(frame[key]) is not str or not HASH_RE.fullmatch(frame[key]) for key in ("binding_digest", "runtime_digest")):
        raise ValueError("managed read-only digest rejected")
    if type(frame["nonce"]) is not str or not UUID_RE.fullmatch(frame["nonce"]):
        raise ValueError("managed read-only nonce rejected")
    return frame


def read_managed_readonly_bootstrap(stream: Any) -> dict[str, Any]:
    raw = stream.readline(MAX_FRAME_BYTES + 1)
    if len(raw) > MAX_FRAME_BYTES or stream.read(1):
        raise ValueError("managed read-only bootstrap must contain exactly one bounded frame")
    return parse_managed_readonly_bootstrap(raw)


def _canonical_absolute_spelling(path: Path) -> Path:
    """Reject relative and lexically aliased spellings before any resolution."""
    raw = os.fspath(path)
    candidate = Path(raw)
    if (type(raw) is not str or not candidate.is_absolute() or str(candidate) != raw
            or any(part in {".", ".."} for part in candidate.parts)):
        raise ValueError("managed read-only entry is outside the bound application")
    return candidate


def _has_linked_path_component(path: Path) -> bool:
    """Check original absolute path components without resolving aliases away."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            return True
    return False


def validate_registered_entry(entry_file: Path, code_root: Path) -> None:
    try:
        expected_path = code_root / "agent-core" / "managed_host_entry.py"
        original_entry = _canonical_absolute_spelling(entry_file)
        original_expected = _canonical_absolute_spelling(expected_path)
        if _has_linked_path_component(original_entry) or _has_linked_path_component(original_expected):
            raise ValueError("managed read-only entry is outside the bound application")
        entry = original_entry.resolve(strict=True)
        expected = original_expected.resolve(strict=True)
    except OSError as exc:
        raise ValueError("managed read-only entry is outside the bound application") from exc
    if str(original_entry) != str(entry) or str(original_expected) != str(expected) or str(entry) != str(expected):
        raise ValueError("managed read-only entry is outside the bound application")


def _read_record(path: Path, limit: int = 32 * 1024 * 1024) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("managed record file rejected")
    raw = path.read_bytes()
    if not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw or len(raw) > limit:
        raise ValueError("managed record frame rejected")
    value = json.loads(raw[:-1].decode("utf-8", "strict"), object_pairs_hook=_reject_duplicate_keys,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    if type(value) is not dict or _canonical(value) + b"\n" != raw:
        raise ValueError("managed record canonical encoding rejected")
    return value


def _record_names(path: Path) -> list[str]:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("managed directory rejected")
    return sorted(item.name for item in path.iterdir())


def _verify_binding_identity_stdlib(namespace: Path, marker: dict[str, Any], binding: dict[str, Any], expected_digest: str) -> None:
    marker_keys = {"format", "mode", "stage", "data_policy", "security_policy", "credentials_policy", "installation_id", "root", "source_roots", "paths"}
    if set(marker) != marker_keys or marker["format"] != 1 or marker["mode"] != "managed_project_local_v1" or marker["stage"] != "prepared_empty":
        raise ValueError("managed namespace marker rejected")
    if marker["data_policy"] != "empty_no_migration" or marker["security_policy"] != "logical_separation_only" or marker["credentials_policy"] != "forbidden":
        raise ValueError("managed namespace policy rejected")
    installation = marker["installation_id"]
    if type(installation) is not str or not UUID_RE.fullmatch(installation) or marker["root"] != str(namespace):
        raise ValueError("managed namespace identity rejected")
    roots = marker["source_roots"]
    if type(roots) is not dict or set(roots) != {"stable", "sandbox"}:
        raise ValueError("managed namespace source roots rejected")
    for source_root in roots.values():
        source = Path(source_root)
        if type(source_root) is not str or not source.is_absolute() or str(source) != source_root or source.resolve(strict=True) != source:
            raise ValueError("managed namespace source path rejected")
    if namespace != Path(roots["sandbox"]) / ".managed-runtime" / installation:
        raise ValueError("managed namespace location rejected")
    if marker["paths"] != {"launcher": str(namespace / "launcher"), "state": str(namespace / "state"), "releases": str(namespace / "releases"),
                           "host_state": str(namespace / "host/state"), "host_profile": str(namespace / "host/profile"),
                           "host_workspace": str(namespace / "host/workspace"), "data": str(namespace / "data/store")}:
        raise ValueError("managed namespace fixed paths rejected")
    binding_keys = {"format", "mode", "installationId", "namespace", "source", "release", "paths", "data", "access", "runtimeRegistration", "writerLease", "readyForLaunch", "bindingDigest"}
    if set(binding) != binding_keys or binding["format"] != 1 or binding["mode"] != "managed_production_binding_v1":
        raise ValueError("managed production binding rejected")
    body = {key: value for key, value in binding.items() if key != "bindingDigest"}
    if hashlib.sha256(_canonical(body)).hexdigest() != expected_digest or binding["bindingDigest"] != expected_digest:
        raise ValueError("managed production binding digest rejected")
    namespace_record = binding["namespace"]
    if type(namespace_record) is not dict or set(namespace_record) != {"mode", "root", "digest", "securityPolicy", "credentialsPolicy"}:
        raise ValueError("managed production namespace binding rejected")
    if namespace_record != {"mode": marker["mode"], "root": str(namespace), "digest": hashlib.sha256(_canonical(marker)).hexdigest(),
                            "securityPolicy": "logical_separation_only", "credentialsPolicy": "forbidden"}:
        raise ValueError("managed production namespace digest mismatch")
    if binding["installationId"] != installation or binding["access"] != "read_only" or binding["runtimeRegistration"] is not None or binding["writerLease"] is not None or binding["readyForLaunch"] is not False:
        raise ValueError("managed production access/readiness rejected")
    source = binding["source"]
    if type(source) is not dict or set(source) != {"operationId", "planDigest", "receiptDigest", "snapshotDigest", "mergedHead", "mergedTree", "roots"}:
        raise ValueError("managed production source record rejected")
    if not UUID_RE.fullmatch(str(source["operationId"])) or any(not HASH_RE.fullmatch(str(source[key])) for key in ("planDigest", "receiptDigest", "snapshotDigest")):
        raise ValueError("managed production source identity rejected")
    if any(not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", str(source[key])) for key in ("mergedHead", "mergedTree")):
        raise ValueError("managed production Git identity rejected")
    if type(source["roots"]) is not dict or set(source["roots"]) != {"stable", "sandbox"}:
        raise ValueError("managed production source roots rejected")
    for side in ("stable", "sandbox"):
        record = source["roots"][side]
        if type(record) is not dict or set(record) != {"worktreeRoot", "gitDir", "commonDir", "branch"} or record["worktreeRoot"] != roots[side] or type(record["branch"]) is not str or not record["branch"]:
            raise ValueError("managed production source root identity rejected")
        for field in ("worktreeRoot", "gitDir", "commonDir"):
            path = Path(record[field])
            if not path.is_absolute() or str(path) != record[field] or path.resolve(strict=True) != path:
                raise ValueError("managed production Git path rejected")
        Path(record["gitDir"]).relative_to(Path(record["commonDir"]))
    if source["roots"]["stable"]["commonDir"] != source["roots"]["sandbox"]["commonDir"]:
        raise ValueError("managed production source Git identity mismatch")
    release = binding["release"]
    if type(release) is not dict or set(release) != {"releaseId", "manifestDigest", "root", "buildReceiptDigest", "toolchainDigest", "buildPolicy"}:
        raise ValueError("managed production release record rejected")
    release_id = release["releaseId"]
    if (not HASH_RE.fullmatch(str(release_id)) or release["manifestDigest"] != release_id or release["root"] != str(namespace / "releases" / release_id)
            or release["buildPolicy"] != "configless_snapshot_v2" or not HASH_RE.fullmatch(str(release["buildReceiptDigest"]))
            or not HASH_RE.fullmatch(str(release["toolchainDigest"]))):
        raise ValueError("managed production release identity rejected")
    data = binding["data"]
    if type(data) is not dict or set(data) != {"storeId", "schema", "revision", "policy"} or not UUID_RE.fullmatch(str(data["storeId"])
            ) or data["schema"] != "managed_empty_store_v1" or data["revision"] != 0 or data["policy"] != "empty_no_migration":
        raise ValueError("managed production store binding rejected")
    fixed = {"launcher": str(namespace / "launcher"), "state": str(namespace / "state"), "hostState": str(namespace / "host/state"),
             "hostProfile": str(namespace / "host/profile"), "hostWorkspace": str(namespace / "host/workspace"),
             "data": str(namespace / "data/store"), "code": str(namespace / "releases" / release_id / "app")}
    if binding["paths"] != fixed:
        raise ValueError("managed production path map rejected")
    for rel in ("launcher", "state", "releases", "host", "host/state", "host/profile", "host/workspace", "data", "data/store", f"releases/{release_id}", f"releases/{release_id}/app"):
        path = namespace / rel
        if path.resolve(strict=True) != path or not path.is_dir() or path.is_symlink():
            raise ValueError("managed production directory layout rejected")
def _verify_binding_stdlib(namespace: Path, marker: dict[str, Any], binding: dict[str, Any], expected_digest: str) -> None:
    _verify_binding_identity_stdlib(namespace, marker, binding, expected_digest)
    release_id = binding["release"]["releaseId"]
    expected_names = {".": ["data", "host", "launcher", "releases", "state"],
                      "state": ["managed-namespace.json", "production-binding.json", "production-context-intent.json", "production-context-receipt.json"],
                      "launcher": ["runtime-intent.json", "runtime-receipt.json", "runtime-registration.json"],
                      "releases": [release_id], "host": ["profile", "state", "workspace"], "data": ["store"]}
    for rel, names in expected_names.items():
        if _record_names(namespace if rel == "." else namespace / rel) != names:
            raise ValueError("managed production namespace contains unknown or missing records")
    for rel in ("host/state", "host/profile", "host/workspace", "data/store"):
        if _record_names(namespace / rel):
            raise ValueError("managed production namespace is not empty")


def _verify_operational_binding_stdlib(namespace, marker, binding, expected_digest):
    _verify_binding_identity_stdlib(namespace, marker, binding, expected_digest)
    expected = {".": {"data", "host", "launcher", "releases", "state"},
                "launcher": {"runtime-intent.json", "runtime-registration.json", "runtime-receipt.json"},
                "releases": {binding["release"]["releaseId"]}, "host": {"profile", "state", "workspace"}, "data": {"store"},
                "host/state": {"managed-host-profile-intent.json"}, "host/workspace": set()}
    for relative, names in expected.items():
        if set(_record_names(namespace if relative == "." else namespace / relative)) != names:
            raise ValueError("managed operational namespace shape rejected")
    base = {"managed-namespace.json", "production-binding.json", "production-context-intent.json", "production-context-receipt.json"}
    names = set(_record_names(namespace / "state"))
    if not base <= names or names - base - {"managed-store-control.sqlite3", "managed-store.lock"}:
        raise ValueError("managed operational state contains unknown records")


def validate_host_profile_intent(namespace, binding, runtime_digest, host_nonce, profile_intent_digest, access):
    value = _read_record(namespace / "host/state/managed-host-profile-intent.json", 16 * 1024)
    keys = {"format", "mode", "installationId", "releaseId", "bindingDigest", "runtimeDigest", "hostNonce", "profileRoot", "access", "intentDigest"}
    if set(value) != keys:
        raise ValueError("managed Host profile intent fields rejected")
    body = {key: item for key, item in value.items() if key != "intentDigest"}
    if (value["format"] != 1 or value["mode"] != "managed_host_profile_intent_v1"
            or value["installationId"] != binding["installationId"] or value["releaseId"] != binding["release"]["releaseId"]
            or value["bindingDigest"] != binding["bindingDigest"] or value["runtimeDigest"] != runtime_digest
            or value["hostNonce"] != host_nonce or not UUID_RE.fullmatch(str(host_nonce))
            or value["profileRoot"] != binding["paths"]["hostProfile"] or value["access"] not in {"read_only", "fenced_application"}
            or (access is not None and value["access"] != access) or value["intentDigest"] != profile_intent_digest
            or hashlib.sha256(_canonical(body)).hexdigest() != profile_intent_digest):
        raise ValueError("managed Host profile intent binding rejected")
    return value


def _validate_runtime_chain_stdlib(registration: dict[str, Any], intent: dict[str, Any], receipt: dict[str, Any], binding: dict[str, Any]) -> None:
    if set(registration) != {"format", "mode", "installationId", "releaseId", "bindingDigest", "runtimes", "readyForLaunch", "runtimeDigest"}:
        raise ValueError("runtime registration schema rejected")
    body = {key: value for key, value in registration.items() if key != "runtimeDigest"}
    runtime_digest = hashlib.sha256(_canonical(body)).hexdigest()
    if (registration["format"] != 1 or registration["mode"] != "managed_runtime_registration_v1" or registration["readyForLaunch"] is not False
            or registration["runtimeDigest"] != runtime_digest or registration["bindingDigest"] != binding["bindingDigest"]
            or registration["installationId"] != binding["installationId"] or registration["releaseId"] != binding["release"]["releaseId"]):
        raise ValueError("runtime registration binding rejected")
    if set(intent) != {"format", "mode", "installationId", "releaseId", "bindingDigest", "runtimeDigest", "state", "readyForLaunch", "intentDigest"}:
        raise ValueError("runtime intent schema rejected")
    intent_body = {key: value for key, value in intent.items() if key != "intentDigest"}
    if (intent["format"] != 1 or intent["mode"] != "managed_runtime_intent_v1" or intent["state"] != "registration_started"
            or intent["readyForLaunch"] is not False or intent["intentDigest"] != hashlib.sha256(_canonical(intent_body)).hexdigest()
            or (intent["installationId"], intent["releaseId"], intent["bindingDigest"], intent["runtimeDigest"]) !=
               (registration["installationId"], registration["releaseId"], registration["bindingDigest"], runtime_digest)):
        raise ValueError("runtime intent binding rejected")
    if set(receipt) != {"format", "mode", "installationId", "releaseId", "bindingDigest", "runtimeDigest", "registrationDigest", "readyForLaunch", "receiptDigest"}:
        raise ValueError("runtime receipt schema rejected")
    receipt_body = {key: value for key, value in receipt.items() if key != "receiptDigest"}
    if (receipt["format"] != 1 or receipt["mode"] != "managed_runtime_receipt_v1" or receipt["readyForLaunch"] is not False
            or receipt["receiptDigest"] != hashlib.sha256(_canonical(receipt_body)).hexdigest()
            or (receipt["installationId"], receipt["releaseId"], receipt["bindingDigest"], receipt["runtimeDigest"]) !=
               (registration["installationId"], registration["releaseId"], registration["bindingDigest"], runtime_digest)
            or receipt["registrationDigest"] != hashlib.sha256(_canonical(registration)).hexdigest()):
        raise ValueError("runtime receipt binding rejected")


def _validate_context_records_stdlib(marker: dict[str, Any], binding: dict[str, Any], intent: dict[str, Any], receipt: dict[str, Any]) -> None:
    if set(intent) != {"format", "mode", "installationId", "operationId", "releaseId", "namespaceDigest", "bindingDigest", "dataStoreId", "state", "readyForLaunch", "intentDigest"}:
        raise ValueError("production context intent schema rejected")
    intent_body = {key: value for key, value in intent.items() if key != "intentDigest"}
    if (intent["format"] != 1 or intent["mode"] != "managed_production_context_intent_v1" or intent["state"] != "copy_started"
            or intent["readyForLaunch"] is not False or intent["intentDigest"] != hashlib.sha256(_canonical(intent_body)).hexdigest()):
        raise ValueError("production context intent digest rejected")
    if set(receipt) != {"format", "mode", "installationId", "operationId", "releaseId", "bindingDigest", "namespaceDigest", "manifestDigest", "dataStoreId", "filesDigest", "payloadBytes", "allocationBytes", "readyForLaunch", "receiptDigest"}:
        raise ValueError("production context receipt schema rejected")
    receipt_body = {key: value for key, value in receipt.items() if key != "receiptDigest"}
    if (receipt["format"] != 1 or receipt["mode"] != "managed_production_context_receipt_v1" or receipt["readyForLaunch"] is not False
            or receipt["receiptDigest"] != hashlib.sha256(_canonical(receipt_body)).hexdigest()):
        raise ValueError("production context receipt digest rejected")
    common = (binding["installationId"], binding["source"]["operationId"], binding["release"]["releaseId"], binding["bindingDigest"],
              hashlib.sha256(_canonical(marker)).hexdigest(), binding["data"]["storeId"])
    for record in (intent, receipt):
        if (record["installationId"], record["operationId"], record["releaseId"], record["bindingDigest"], record["namespaceDigest"], record["dataStoreId"]) != common:
            raise ValueError("production context record binding rejected")
    if receipt["manifestDigest"] != binding["release"]["manifestDigest"] or not HASH_RE.fullmatch(str(receipt["filesDigest"])):
        raise ValueError("production context payload receipt rejected")
    if type(receipt["payloadBytes"]) is not int or type(receipt["allocationBytes"]) is not int or not 0 <= receipt["payloadBytes"] <= 64 * 1024 * 1024 or receipt["allocationBytes"] < receipt["payloadBytes"]:
        raise ValueError("production context receipt budget rejected")


def _verify_sealed_application(namespace: Path, binding: dict[str, Any], context_receipt: dict[str, Any] | None = None) -> Path:
    release_id = binding["release"]["releaseId"]
    release_root = namespace / "releases" / release_id
    if _record_names(release_root) != ["app", "manifest.json", "seal-intent.json", "sealed.json"]:
        raise ValueError("sealed application root layout rejected")
    manifest = _read_record(release_root / "manifest.json", 4 * 1024 * 1024)
    manifest_keys = {"format", "mode", "protocol", "source", "build", "data", "files", "readyForLaunch"}
    if set(manifest) != manifest_keys or manifest["format"] != 1 or manifest["mode"] != "managed_postmerge_release_v1" or manifest["protocol"] != 1 or manifest["readyForLaunch"] is not False:
        raise ValueError("sealed application manifest schema rejected")
    if hashlib.sha256(_canonical(manifest)).hexdigest() != release_id or release_id != binding["release"]["manifestDigest"]:
        raise ValueError("sealed application manifest digest rejected")
    source = manifest["source"]
    build = manifest["build"]
    data = manifest["data"]
    if type(source) is not dict or set(source) != {"snapshotDigest", "planDigest", "receiptDigest", "mergedHead", "mergedTree"}:
        raise ValueError("sealed source manifest rejected")
    if type(build) is not dict or set(build) != {"receiptDigest", "toolchainDigest", "policy"}:
        raise ValueError("sealed build manifest rejected")
    if source != {key: binding["source"][key] for key in ("snapshotDigest", "planDigest", "receiptDigest", "mergedHead", "mergedTree")}:
        raise ValueError("sealed source provenance differs from binding")
    if build != {"receiptDigest": binding["release"]["buildReceiptDigest"], "toolchainDigest": binding["release"]["toolchainDigest"], "policy": binding["release"]["buildPolicy"]}:
        raise ValueError("sealed build provenance differs from binding")
    if data != {"policy": "empty_no_migration", "schema": "managed_empty_store_v1", "revisionBefore": 0}:
        raise ValueError("sealed application data policy rejected")
    files = manifest["files"]
    if type(files) is not list or not files or len(files) > 20_000:
        raise ValueError("sealed application inventory rejected")
    expected: dict[str, dict[str, Any]] = {}
    folded_paths: set[str] = set()
    total = 0
    for item in files:
        if type(item) is not dict or set(item) != {"path", "mode", "sha256", "byteLength"}:
            raise ValueError("sealed application file entry rejected")
        relative = item["path"]
        if (not _safe_relative(relative)
                or any(part in {"", ".", ".."} for part in relative.split("/")) or item["mode"] not in {"100644", "100755"}
                or type(item["sha256"]) is not str or not HASH_RE.fullmatch(item["sha256"])
                or type(item["byteLength"]) is not int or item["byteLength"] < 0):
            raise ValueError("sealed application file metadata invalid")
        key = relative.casefold()
        if key in folded_paths:
            raise ValueError("sealed application path collision")
        folded_paths.add(key)
        expected[relative] = item
        total += item["byteLength"]
        if total > 64 * 1024 * 1024:
            raise ValueError("sealed application byte limit exceeded")
    required = {"agent-core/managed_host_entry.py", "agent-core/managed_readonly.py"}
    if not required.issubset(expected):
        raise ValueError("sealed application lacks the managed read-only entry")
    app_root = release_root / "app"
    actual_files: dict[str, tuple[int, str]] = {}
    actual_dirs: set[str] = set()
    pending = [(app_root, "")]
    while pending:
        directory, parent = pending.pop()
        with os.scandir(directory) as scan:
            children = list(scan)
        for child in children:
            relative = f"{parent}/{child.name}" if parent else child.name
            child_path = Path(child.path)
            info = child_path.lstat()
            if child.is_symlink() or getattr(Path(child.path), "is_junction", lambda: False)():
                raise ValueError("sealed application tree contains a link")
            if stat.S_ISDIR(info.st_mode):
                if info.st_nlink < 1:
                    raise ValueError("sealed application directory link count rejected")
                actual_dirs.add(relative)
                pending.append((child_path, relative))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if info.st_size > 64 * 1024 * 1024:
                    raise ValueError("sealed application file too large")
                digest = hashlib.sha256()
                size = 0
                with child_path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        size += len(block)
                        digest.update(block)
                actual_files[relative] = (size, digest.hexdigest())
            else:
                raise ValueError("sealed application tree contains a special file")
    if set(actual_files) != set(expected):
        raise ValueError("sealed application file set differs from manifest")
    required_dirs = {"/".join(path.split("/")[:index]) for path in expected for index in range(1, len(path.split("/")))}
    if actual_dirs != required_dirs:
        raise ValueError("sealed application directory set differs from manifest")
    for relative, record in expected.items():
        if actual_files[relative] != (record["byteLength"], record["sha256"]):
            raise ValueError("sealed application file digest differs from manifest")
    seal_intent = _read_record(release_root / "seal-intent.json", 64 * 1024)
    seal_receipt = _read_record(release_root / "sealed.json", 64 * 1024)
    intent_keys = {"format", "mode", "operationId", "releaseId", "manifestDigest", "state", "intentDigest"}
    receipt_keys = {"format", "mode", "operationId", "releaseId", "manifestDigest", "filesDigest", "readyForLaunch", "receiptDigest"}
    if set(seal_intent) != intent_keys or seal_intent["format"] != 1 or seal_intent["mode"] != "managed_postmerge_release_seal_intent_v1" or seal_intent["releaseId"] != release_id or seal_intent["manifestDigest"] != release_id or seal_intent["state"] != "started" or not UUID_RE.fullmatch(str(seal_intent["operationId"])):
        raise ValueError("sealed application intent rejected")
    if seal_intent["intentDigest"] != hashlib.sha256(_canonical({key: value for key, value in seal_intent.items() if key != "intentDigest"})).hexdigest():
        raise ValueError("sealed application intent digest rejected")
    if set(seal_receipt) != receipt_keys or seal_receipt["format"] != 1 or seal_receipt["mode"] != "managed_postmerge_release_seal_receipt_v1" or seal_receipt["operationId"] != seal_intent["operationId"] or seal_receipt["releaseId"] != release_id or seal_receipt["manifestDigest"] != release_id or seal_receipt["filesDigest"] != hashlib.sha256(_canonical(files)).hexdigest() or seal_receipt["readyForLaunch"] is not False:
        raise ValueError("sealed application receipt rejected")
    if seal_receipt["receiptDigest"] != hashlib.sha256(_canonical({key: value for key, value in seal_receipt.items() if key != "receiptDigest"})).hexdigest():
        raise ValueError("sealed application receipt digest rejected")
    if context_receipt is not None:
        # Node's context receipt binds captureTree(releaseRoot): app files in
        # their already sealed producer order, then these three metadata files.
        # The seal receipt above separately binds manifest.files. Do not sort
        # the producer array using Python/OS collation or confuse the digests.
        inventory = []
        for item in files:
            file_path = app_root.joinpath(*item["path"].split("/"))
            inventory.append({**item, "path": "app/" + item["path"],
                              "mode": "100755" if file_path.lstat().st_mode & 0o111 else "100644"})
        for name in ("manifest.json", "seal-intent.json", "sealed.json"):
            metadata_path = release_root / name
            info = metadata_path.lstat()
            raw = metadata_path.read_bytes()
            inventory.append({"path": name, "mode": "100755" if info.st_mode & 0o111 else "100644",
                              "sha256": hashlib.sha256(raw).hexdigest(), "byteLength": len(raw)})
        if (context_receipt.get("filesDigest") != hashlib.sha256(_canonical(inventory)).hexdigest()
                or type(context_receipt.get("payloadBytes")) is not int or context_receipt["payloadBytes"] != total):
            raise ValueError("production context receipt does not match complete sealed release inventory")
    return app_root


def _safe_metadata_error(code: str) -> int:
    print(json.dumps({"ready": False, "error": code}, separators=(",", ":")), flush=True)
    return 4


def _runtime_layout(registration: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    runtimes = registration["runtimes"]
    python = runtimes["python"]
    python_base = runtimes["pythonBase"]
    return {
        "protocol": "my-agent-fixed-layout-v1",
        "code_root": binding["paths"]["code"],
        # The helper's private RuntimeContext needs a second non-overlapping
        # layout root; host/workspace is the registered, empty instance root.
        "instance_root": binding["paths"]["hostWorkspace"],
        "runtime": {
            "python_executable": str(Path(python["root"]) / Path(python["entry"])),
            "python_root": python["root"],
            "python_base_root": python_base["root"],
        },
    }


def _runtime_audit_roots(runtime_context) -> tuple[Path, ...]:
    # Fixed runtime validation also records CPython's optional, possibly absent
    # pythonXY.zip sys.path placeholder. Existing registered runtime directories
    # already cover real archives; never resolve a placeholder as a read root.
    return tuple(runtime_context.allowed_roots[1:])


def run_cold_server(frame: dict[str, Any], host: str, port: int) -> int:
    try:
        namespace_root = Path(frame["namespace_root"])
        marker = _read_record(namespace_root / "state" / "managed-namespace.json")
        binding = _read_record(namespace_root / "state" / "production-binding.json")
        _verify_binding_stdlib(namespace_root, marker, binding, frame["binding_digest"])
        context_intent = _read_record(namespace_root / "state" / "production-context-intent.json")
        context_receipt = _read_record(namespace_root / "state" / "production-context-receipt.json")
        _validate_context_records_stdlib(marker, binding, context_intent, context_receipt)
        registration = _read_record(namespace_root / "launcher" / "runtime-registration.json")
        intent = _read_record(namespace_root / "launcher" / "runtime-intent.json")
        receipt = _read_record(namespace_root / "launcher" / "runtime-receipt.json")
        _validate_runtime_chain_stdlib(registration, intent, receipt, binding)
        if registration["runtimeDigest"] != frame["runtime_digest"]:
            raise ValueError("runtime digest differs from bootstrap")
        layout = _runtime_layout(registration, binding)
        if sys.flags.isolated != 1 or sys.flags.no_site != 1 or not sys.dont_write_bytecode:
            raise ValueError("Python must run with -I -B -S")
        if os.path.normcase(str(Path(sys.executable).resolve(strict=True))) != os.path.normcase(str(Path(layout["runtime"]["python_executable"]).resolve(strict=True))):
            raise ValueError("running Python differs from registration")

        code_root = Path(binding["paths"]["code"]).resolve(strict=True)
        validate_registered_entry(Path(__file__), code_root)
        verified_app_root = _verify_sealed_application(namespace_root, binding, context_receipt).resolve(strict=True)
        if verified_app_root != code_root:
            raise ValueError("managed entry code root differs from verified release")
        agent_core = (code_root / "agent-core").resolve(strict=True)
        if str(agent_core) not in sys.path:
            sys.path.insert(0, str(agent_core))

        # Only after the stdlib verifier has hashed the manifest and complete
        # release tree may code from that tree be imported.
        from managed_readonly import (
            ManagedReadonlyAuditGuard,
            ManagedReadonlyContext,
            _parse_canonical_record,
            read_roots_for_context,
            validate_production_binding,
            validate_production_context_record_chain,
            validate_runtime_record_chain,
            verify_runtime_inventory,
        )

        validate_production_binding(namespace_root, marker, binding, frame["binding_digest"])
        validate_production_context_record_chain(marker, binding, context_intent, context_receipt)
        validate_runtime_record_chain(registration, intent, receipt, frame["binding_digest"],
                                      installation_id=binding["installationId"], release_id=binding["release"]["releaseId"])
        runtime_roots = verify_runtime_inventory(registration)
        fixed_entry = importlib.import_module("fixed_host_entry")
        runtime_context = fixed_entry.validate_runtime_layout(layout, fixed_entry._runtime_info())
        fixed_entry.add_registered_import_paths(sys.path, runtime_context)
        context = ManagedReadonlyContext._from_validated_records(
            namespace_root=namespace_root, binding=binding, runtime_digest=registration["runtimeDigest"],
            nonce=frame["nonce"],
            record_paths=(namespace_root / "state/managed-namespace.json", namespace_root / "state/production-binding.json",
                          namespace_root / "state/production-context-intent.json", namespace_root / "state/production-context-receipt.json",
                          namespace_root / "launcher/runtime-intent.json", namespace_root / "launcher/runtime-registration.json",
                          namespace_root / "launcher/runtime-receipt.json"),
        )
        context.revalidate()
        allowed_roots = read_roots_for_context(context, _runtime_audit_roots(runtime_context))
        ManagedReadonlyAuditGuard(allowed_roots).install()
        server_module = importlib.import_module("server")
        fixed_entry.validate_module_snapshot(
            fixed_entry._snapshot_loaded_modules(), roots=runtime_context.allowed_roots,
            archives=runtime_context.allowed_archives,
        )
        return __import__("asyncio").run(server_module.run_server(host, port, managed_readonly_context=context))
    except Exception:
        return _safe_metadata_error("managed_readonly_bootstrap_invalid")


def _parse_explicit_managed_bootstrap(raw, protocol, extra):
    if type(raw) is not bytes or len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
        raise ValueError("managed explicit bootstrap frame rejected")
    frame = json.loads(raw[:-1].decode("utf-8", "strict"), object_pairs_hook=_reject_duplicate_keys)
    base = {"protocol", "namespace_root", "binding_digest", "runtime_digest", "nonce"}
    if type(frame) is not dict or set(frame) != base | extra or frame["protocol"] != protocol or _canonical(frame) + b"\n" != raw:
        raise ValueError("managed explicit bootstrap fields rejected")
    projected = {key: frame[key] for key in base}
    projected["protocol"] = "my-agent-managed-readonly-v1"
    parse_managed_readonly_bootstrap(_canonical(projected) + b"\n")
    if not UUID_RE.fullmatch(str(frame["host_nonce"])) or not HASH_RE.fullmatch(str(frame["profile_intent_digest"])):
        raise ValueError("managed Host bootstrap identity rejected")
    return frame


def parse_managed_application_bootstrap(raw):
    frame = _parse_explicit_managed_bootstrap(raw, "my-agent-managed-application-v1",
        {"operation_id", "control_token", "host_nonce", "profile_intent_digest"})
    if not UUID_RE.fullmatch(str(frame["operation_id"])) or type(frame["control_token"]) is not str or not HASH_RE.fullmatch(frame["control_token"]):
        raise ValueError("managed application authority rejected")
    return frame


def read_managed_application_bootstrap(stream):
    # Application control deliberately retains stdin after the first frame.
    return parse_managed_application_bootstrap(stream.readline(MAX_FRAME_BYTES + 1))


def parse_managed_host_readonly_bootstrap(raw):
    frame = _parse_explicit_managed_bootstrap(raw, "my-agent-managed-host-readonly-v1", {"host_nonce", "profile_intent_digest", "control_token"})
    if type(frame["control_token"]) is not str or not HASH_RE.fullmatch(frame["control_token"]):
        raise ValueError("managed Host readonly control token rejected")
    return frame


def _validate_operational_records(namespace, binding, runtime_digest):
    marker = _read_record(namespace / "state/managed-namespace.json")
    _validate_context_records_stdlib(marker, binding, _read_record(namespace / "state/production-context-intent.json"),
                                     _read_record(namespace / "state/production-context-receipt.json"))
    registration = _read_record(namespace / "launcher/runtime-registration.json")
    _validate_runtime_chain_stdlib(registration, _read_record(namespace / "launcher/runtime-intent.json"),
                                   _read_record(namespace / "launcher/runtime-receipt.json"), binding)
    if registration["runtimeDigest"] != runtime_digest:
        raise ValueError("managed operational runtime digest changed")
    return registration


def run_operational_server(frame, host, port, *, application_mode):
    try:
        namespace = Path(frame["namespace_root"])
        marker, binding = _read_record(namespace / "state/managed-namespace.json"), _read_record(namespace / "state/production-binding.json")
        _verify_operational_binding_stdlib(namespace, marker, binding, frame["binding_digest"])
        validate_host_profile_intent(namespace, binding, frame["runtime_digest"], frame["host_nonce"], frame["profile_intent_digest"],
                                     "fenced_application" if application_mode else None)
        registration = _validate_operational_records(namespace, binding, frame["runtime_digest"])
        layout = _runtime_layout(registration, binding)
        if sys.flags.isolated != 1 or sys.flags.no_site != 1 or not sys.dont_write_bytecode:
            raise ValueError("Python must run with -I -B -S")
        if str(Path(sys.executable).resolve(strict=True)) != str(Path(layout["runtime"]["python_executable"]).resolve(strict=True)):
            raise ValueError("managed Python executable mismatch")
        code_root = Path(binding["paths"]["code"])
        validate_registered_entry(Path(__file__), code_root)
        verified = _verify_sealed_application(namespace, binding, _read_record(namespace / "state/production-context-receipt.json"))
        if str(verified) != str(code_root) or not (code_root / "agent-core/managed_application.py").is_file() or not (code_root / "agent-core/managed_store.py").is_file():
            raise ValueError("managed operational sealed payload is incomplete")
        sys.path.insert(0, str(code_root / "agent-core"))
        from managed_readonly import verify_runtime_inventory, ManagedReadonlyAuditGuard
        verify_runtime_inventory(registration)
        fixed_entry = importlib.import_module("fixed_host_entry")
        runtime_context = fixed_entry.validate_runtime_layout(layout, fixed_entry._runtime_info())
        fixed_entry.add_registered_import_paths(sys.path, runtime_context)
        from managed_application import ManagedApplication, ManagedApplicationAuditGuard, ManagedHostReadonlyContext
        from managed_store import ManagedStore
        from dataclasses import replace
        context = ManagedHostReadonlyContext._from_validated_records(namespace_root=namespace, binding=binding,
                    runtime_digest=frame["runtime_digest"], nonce=frame["nonce"])
        context = replace(context, host_nonce=frame["host_nonce"], profile_intent_digest=frame["profile_intent_digest"], _control_token=frame["control_token"])
        roots = context.registered_read_roots(_runtime_audit_roots(runtime_context))
        if application_mode:
            store = ManagedStore(str(namespace), installation_id=binding["installationId"], data_store_id=binding["data"]["storeId"],
                                 release_id=binding["release"]["releaseId"], binding_digest=binding["bindingDigest"])
            guard = ManagedApplicationAuditGuard(roots, store)
            guard.install()
            app = ManagedApplication.open(store, operation_id=frame["operation_id"], nonce=frame["nonce"], runtime_digest=frame["runtime_digest"],
                                           control_token=frame["control_token"], audit_guard=guard)
            server = importlib.import_module("server")
            return __import__("asyncio").run(server.run_server(host, port, managed_application=app, managed_control_stream=sys.stdin.buffer))
        context.revalidate()
        ManagedReadonlyAuditGuard(roots).install()
        server = importlib.import_module("server")
        return __import__("asyncio").run(server.run_server(host, port, managed_readonly_context=context, managed_control_stream=sys.stdin.buffer))
    except Exception:
        return _safe_metadata_error("managed_application_maintenance_required" if application_mode else "managed_host_readonly_bootstrap_invalid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Managed read-only Python server entry")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--managed-application", action="store_true")
    modes.add_argument("--managed-host-readonly", action="store_true")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "::1"} or not 0 <= args.port <= 65535:
        return _safe_metadata_error("managed_readonly_bootstrap_invalid")
    try:
        if args.managed_application:
            frame = read_managed_application_bootstrap(sys.stdin.buffer)
            return run_operational_server(frame, args.host, args.port, application_mode=True)
        if args.managed_host_readonly:
            raw = sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1)
            frame = parse_managed_host_readonly_bootstrap(raw)
            return run_operational_server(frame, args.host, args.port, application_mode=False)
        frame = read_managed_readonly_bootstrap(sys.stdin.buffer)
    except Exception:
        return _safe_metadata_error("managed_readonly_bootstrap_invalid")
    return run_cold_server(frame, args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
