"""One-shot stdin/stdout adapter for the trusted WorkspaceBroker worker."""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any

from evolution.contracts import ContractError, canonical_json, parse_json_bytes
from evolution.workspace import WorkspaceError, _has_reparse_component, apply_patch, capture_snapshot, inspect_source, trusted_sandbox_root

_MAX_REQUEST = 65_536
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _read_scope_config() -> dict[str, Any]:
    configured = os.environ.get("EVOLUTION_WORKSPACE_CONFIG", "")
    if not configured or not Path(configured).is_absolute():
        raise WorkspaceError("permission_denied", "trusted workspace config is not configured")
    path = Path(os.path.abspath(configured))
    sandbox = trusted_sandbox_root()
    artifacts = (sandbox / "artifacts").resolve(strict=True)
    try:
        if os.path.commonpath((os.path.normcase(str(artifacts)),os.path.normcase(str(path)))) != os.path.normcase(str(artifacts)):
            raise WorkspaceError("permission_denied", "workspace config must remain under sandbox/artifacts")
        if _has_reparse_component(path, include_final=True):
            raise WorkspaceError("scope_conflict", "workspace config contains a reparse point")
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise WorkspaceError("scope_conflict", "workspace config is not a regular file")
        raw = path.read_bytes()
        expected_digest = os.environ.get("EVOLUTION_WORKSPACE_CONFIG_SHA256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or hashlib.sha256(raw).hexdigest() != expected_digest:
            raise WorkspaceError("scope_conflict", "workspace config digest does not match trusted bootstrap")
    except OSError as exc:
        raise WorkspaceError("permission_denied", "workspace config could not be read") from exc
    scope = parse_json_bytes(raw)
    if type(scope) is not dict:
        raise WorkspaceError("invalid_request", "workspace config must be an object")
    return scope


def _dispatch(scope: dict[str, Any], envelope: Any) -> dict[str, Any]:
    if type(envelope) is not dict or set(envelope) != {"operation_id", "action", "payload"}:
        raise WorkspaceError("invalid_request", "worker envelope fields are invalid")
    operation_id = envelope["operation_id"]
    if not isinstance(operation_id,str) or not _UUID.fullmatch(operation_id):
        raise WorkspaceError("invalid_request", "worker operation_id must be a canonical UUID")
    action = envelope["action"]
    payload = envelope["payload"]
    if type(payload) is not dict:
        raise WorkspaceError("invalid_request", "worker payload must be an object")
    if action == "inspect":
        result = inspect_source(scope, payload)
    elif action == "patch":
        if "operation_id" in payload:
            raise WorkspaceError("invalid_request", "operation_id is supplied only by the trusted worker envelope")
        result = apply_patch(scope, {"operation_id":envelope["operation_id"], **payload})
    elif action == "snapshot":
        if set(payload) != {"workspace_revision", "include_paths"}:
            raise WorkspaceError("invalid_request", "snapshot payload fields are invalid")
        result = capture_snapshot(scope, payload["include_paths"], expected_revision=payload["workspace_revision"])
    else:
        raise WorkspaceError("unsupported_capability", "worker action is not available in slice A")
    return result


def main() -> int:
    try:
        scope = _read_scope_config()
        raw = sys.stdin.buffer.read(_MAX_REQUEST + 1)
        if len(raw) > _MAX_REQUEST:
            raise WorkspaceError("budget_exceeded", "worker request exceeds 64 KiB")
        envelope = parse_json_bytes(raw)
        result = _dispatch(scope, envelope)
        output = canonical_json(result)
        if len(output) > _MAX_REQUEST:
            raise WorkspaceError("budget_exceeded", "worker result exceeds 64 KiB")
        sys.stdout.buffer.write(output + b"\n")
        sys.stdout.buffer.flush()
        return 0
    except (WorkspaceError, ContractError) as exc:
        code = getattr(exc, "code", "invalid_request")
        message = str(exc).replace("\r", " ").replace("\n", " ")[:512]
        try:
            output = canonical_json({"ok":False,"error":{"code":code,"message":message}})
            sys.stdout.buffer.write(output + b"\n")
            sys.stdout.buffer.flush()
        except Exception:
            pass
        return 2
    except Exception:
        # Never print arbitrary exception text: it may contain source bytes or private paths.
        sys.stderr.write("workspace worker failed\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
