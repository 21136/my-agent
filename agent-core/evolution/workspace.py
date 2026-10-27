"""Bounded source inspection, hash-checked writes, and immutable source snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from evolution.contracts import ContractError, canonical_json, content_hash
from evolution.windows_files import WorkspaceFileError, exclusive_file

_MAX_CONTROL_BYTES = 65_536
_MAX_TEXT_BYTES = 32_768
_MAX_FILES = 256
_MAX_TOTAL_SNAPSHOT_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_FILE_BYTES = 16 * 1024 * 1024
_SCOPE_KEYS = {"scope_id", "source_root", "owned_paths", "state_root", "read_only_roots"}
_PATH_COMPONENT_DENY = {".git", "data", "env", ".env", ".venv", "node_modules", "__pycache__", "artifacts"}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(rb"(?i)(?:api[_-]?key|password|client[_-]?secret|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_+/=-]{20,}"),
)


class WorkspaceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise WorkspaceError(code, message)

def trusted_sandbox_root() -> Path:
    configured = os.environ.get("EVOLUTION_SANDBOX_ROOT")
    if not configured:
        return Path(__file__).resolve().parents[2]
    if not Path(configured).is_absolute():
        _fail("permission_denied", "trusted sandbox root must be absolute")
    root = Path(os.path.abspath(configured))
    if _has_reparse_component(root, include_final=True) or not root.is_dir():
        _fail("permission_denied", "trusted sandbox root is not a real directory")
    return root


def _absolute_inside_sandbox(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        _fail("invalid_request", f"{label} must be absolute")
    lexical = Path(os.path.abspath(value))
    sandbox = trusted_sandbox_root()
    try:
        if os.path.commonpath((os.path.normcase(str(sandbox)), os.path.normcase(str(lexical)))) != os.path.normcase(str(sandbox)):
            _fail("permission_denied", f"{label} must remain inside the experiment sandbox")
    except ValueError:
        _fail("permission_denied", f"{label} is on another volume")
    if _has_reparse_component(lexical, include_final=True, allow_missing=True):
        _fail("scope_conflict", f"{label} contains a reparse point")
    return lexical


def _has_reparse_component(path: Path, *, include_final: bool, allow_missing: bool = False) -> bool:
    parts = path.parts
    cursor = Path(parts[0])
    stop = len(parts) if include_final else max(1, len(parts) - 1)
    for part in parts[1:stop]:
        cursor = cursor / part
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            if allow_missing:
                return False
            raise
        attrs = getattr(info, "st_file_attributes", 0)
        is_junction = getattr(cursor, "is_junction", lambda: False)()
        if os.path.islink(cursor) or is_junction or attrs & 0x400:
            return True
    return False


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8", "strict")) > 4_096:
        _fail("scope_conflict", "path is empty or exceeds the path limit")
    if value.startswith(("/", "\\")) or "\\" in value or ":" in value or "\0" in value:
        _fail("scope_conflict", "path must be relative POSIX form")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("scope_conflict", "path traversal or ambiguous path component")
    if any(part.casefold() in _PATH_COMPONENT_DENY for part in parts):
        _fail("permission_denied", "path is excluded from the evolution source scope")
    return value


def _normalize_scope(scope: Any) -> dict[str, Any]:
    if type(scope) is not dict or set(scope) != _SCOPE_KEYS:
        _fail("invalid_request", "scope fields do not match WorkspaceScope")
    if not isinstance(scope["scope_id"], str) or not _UUID.fullmatch(scope["scope_id"]):
        _fail("invalid_request", "scope_id must be a canonical UUID")
    source = _absolute_inside_sandbox(scope["source_root"], "source_root")
    state = _absolute_inside_sandbox(scope["state_root"], "state_root")
    if not source.is_dir():
        _fail("scope_conflict", "source_root must be an existing real directory")
    sandbox=trusted_sandbox_root()
    source_rel=source.relative_to(sandbox)
    if any(part.casefold() in _PATH_COMPONENT_DENY - {"artifacts"} for part in source_rel.parts):
        _fail("permission_denied", "source_root is inside an excluded system/data directory")
    artifacts=(sandbox/"artifacts").resolve(strict=True)
    try:
        if os.path.commonpath((os.path.normcase(str(artifacts)),os.path.normcase(str(state))))!=os.path.normcase(str(artifacts)):
            _fail("permission_denied", "state_root must remain under sandbox/artifacts")
    except ValueError:
        _fail("permission_denied", "state_root must remain on the sandbox volume")
    if source == state or source in state.parents or state in source.parents:
        _fail("permission_denied", "source_root and state_root must be disjoint")
    owned = scope["owned_paths"]
    if type(owned) is not list or not 1 <= len(owned) <= _MAX_FILES:
        _fail("invalid_request", "owned_paths must contain 1..256 files")
    owned_paths = [_safe_relative(item) for item in owned]
    folded = [item.lower() for item in owned_paths]
    if len(set(folded)) != len(folded):
        _fail("invalid_request", "owned_paths contains duplicate/case-colliding paths")
    read_only = scope["read_only_roots"]
    if type(read_only) is not list or len(read_only) > 32:
        _fail("invalid_request", "read_only_roots must be a bounded list")
    normalized_read_only = []
    for item in read_only:
        if not isinstance(item, str) or not Path(item).is_absolute():
            _fail("invalid_request", "read_only_roots entries must be absolute")
        normalized_read_only.append(Path(os.path.abspath(item)))
    for readonly in normalized_read_only:
        try:
            if os.path.commonpath((os.path.normcase(str(readonly)),os.path.normcase(str(source))))==os.path.normcase(str(readonly)):
                _fail("permission_denied", "source_root overlaps a read-only root")
        except ValueError:
            continue
    return {"scope_id":scope["scope_id"],"source_root":source,"state_root":state,
            "owned_paths":owned_paths,"owned_casefold":set(folded),"read_only_roots":normalized_read_only}


def _ensure_dir(path: Path) -> None:
    if _has_reparse_component(path, include_final=True, allow_missing=True):
        _fail("scope_conflict", "directory path contains a reparse point")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or _has_reparse_component(path, include_final=True):
        _fail("scope_conflict", "directory is not a verified real directory")


def _resolve_owned(scope: dict[str, Any], relative: Any) -> tuple[str, Path]:
    rel = _safe_relative(relative)
    if rel not in scope["owned_paths"]:
        _fail("permission_denied", "path is outside the exact confirmed owned_paths spelling")
    target = scope["source_root"].joinpath(*rel.split("/"))
    try:
        if os.path.commonpath((os.path.normcase(str(scope["source_root"])), os.path.normcase(str(target)))) != os.path.normcase(str(scope["source_root"])):
            _fail("scope_conflict", "resolved path escaped source_root")
    except ValueError:
        _fail("scope_conflict", "resolved path is on a different volume")
    if _has_reparse_component(target, include_final=True, allow_missing=True):
        _fail("scope_conflict", "path traverses a symlink or reparse point")
    for readonly in scope["read_only_roots"]:
        try:
            if os.path.commonpath((os.path.normcase(str(readonly)),os.path.normcase(str(target))))==os.path.normcase(str(readonly)):
                _fail("permission_denied", "owned path overlaps a read-only root")
        except ValueError:
            continue
    return rel, target


def _read_source(scope: dict[str, Any], relative: str, *, missing_ok: bool = False) -> bytes | None:
    _, target = _resolve_owned(scope, relative)
    try:
        with exclusive_file(target, create=False) as handle:
            return handle.read_bytes(max_bytes=_MAX_SOURCE_FILE_BYTES)
    except WorkspaceFileError as exc:
        if missing_ok and getattr(exc, "winerror", None) == 2:
            return None
        raise WorkspaceError(exc.code, str(exc)) from exc
    except FileNotFoundError:
        if missing_ok:
            return None
        _fail("scope_conflict", "owned file does not exist")
    except OSError as exc:
        _fail("scope_conflict", "owned file could not be read through an exclusive handle")


@contextmanager
def _workspace_lease(scope: dict[str, Any]) -> Iterator[tuple[Any, dict[str, Any]]]:
    _ensure_dir(scope["state_root"])
    lock_path = scope["state_root"] / "workspace.lock"
    if not lock_path.exists():
        try:
            with exclusive_file(lock_path, create=True) as created:
                created.write_bytes(b"0\n")
        except WorkspaceFileError:
            if not lock_path.exists():
                raise
    try:
        with exclusive_file(lock_path, create=False) as handle:
            raw = handle.read_bytes(max_bytes=256)
            if raw == b"0\n":
                state = {"workspace_revision":0,"source_fingerprint":None}
            else:
                try:
                    state = json.loads(raw.decode("ascii"))
                except Exception as exc:
                    raise WorkspaceError("recovery_required", "workspace revision record is corrupt") from exc
                if type(state) is not dict or set(state) not in ({"workspace_revision"},{"workspace_revision","source_fingerprint"}) or type(state.get("workspace_revision")) is not int or state["workspace_revision"] < 0:
                    _fail("recovery_required", "workspace revision record is invalid")
                if "source_fingerprint" not in state:
                    state["source_fingerprint"]="legacy-unobserved"
                elif state["source_fingerprint"] is not None and (not isinstance(state["source_fingerprint"],str) or not re.fullmatch(r"sha256:[0-9a-f]{64}",state["source_fingerprint"])):
                    _fail("recovery_required", "workspace source fingerprint is invalid")
            yield handle, state
    except WorkspaceFileError as exc:
        raise WorkspaceError(exc.code, str(exc)) from exc


def _write_workspace_state(handle: Any, revision: int, fingerprint: str | None) -> None:
    value={"workspace_revision":revision,"source_fingerprint":fingerprint}
    handle.write_bytes(json.dumps(value,sort_keys=True,separators=(",",":" )).encode("ascii"))
    handle.flush()


def _source_inventory(scope: dict[str, Any]) -> str:
    files=[]
    total=0
    for relative in sorted(scope["owned_paths"],key=lambda item:item.lower()):
        data=_read_source(scope,relative,missing_ok=True)
        if data is None:
            files.append({"path":relative,"exists":False})
            continue
        total+=len(data)
        if total>_MAX_TOTAL_SNAPSHOT_BYTES:_fail("budget_exceeded","owned source inventory exceeds 64 MiB")
        files.append({"path":relative,"exists":True,"byte_length":len(data),"sha256":_digest(data)})
    return content_hash({"scope_id":scope["scope_id"],"files":files})


def _observe_workspace(scope: dict[str, Any], handle: Any, state: dict[str, Any]) -> dict[str, Any]:
    fingerprint=_source_inventory(scope)
    prior=state["source_fingerprint"]
    if prior is None:
        next_state={"workspace_revision":state["workspace_revision"],"source_fingerprint":fingerprint}
        _write_workspace_state(handle,next_state["workspace_revision"],fingerprint)
        return next_state
    if prior!=fingerprint:
        next_state={"workspace_revision":state["workspace_revision"]+1,"source_fingerprint":fingerprint}
        _write_workspace_state(handle,next_state["workspace_revision"],fingerprint)
        return next_state
    return state

def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _contains_secret(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in _SECRET_PATTERNS)


def _error(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", "scope_conflict")
    if code not in {"invalid_request","permission_denied","scope_conflict","budget_exceeded","unsupported_capability","outcome_unknown","identity_mismatch","recovery_required"}:
        code = "scope_conflict"
    return {"ok":False,"error":{"code":code,"message":str(exc)[:512]}}


def inspect_source(scope: dict[str, Any], query: dict[str, Any]) -> dict[str, Any]:
    try:
        config = _normalize_scope(scope)
        if type(query) is not dict or not isinstance(query.get("query"), str):
            _fail("invalid_request", "inspect query must be an object with query")
        if set(query) - {"query","path"}:
            _fail("invalid_request", "inspect query has unknown fields")
        with _workspace_lease(config) as (lease, state):
            state=_observe_workspace(config,lease,state)
            revision=state["workspace_revision"]
            if query["query"] == "summary":
                if set(query)!={"query"}: _fail("invalid_request","summary inspect accepts only query")
                return {"ok":True,"scope_id":config["scope_id"],"workspace_revision":revision,"owned_file_count":len(config["owned_paths"])}
            if query["query"] == "source_list":
                if set(query)!={"query"}: _fail("invalid_request","source_list inspect accepts only query")
                files=[];total=0
                for rel in config["owned_paths"]:
                    data=_read_source(config,rel,missing_ok=True)
                    if data is not None:
                        if _contains_secret(data): _fail("permission_denied","source list contains a secret-like file")
                        total+=len(data)
                        if total>_MAX_TOTAL_SNAPSHOT_BYTES:_fail("budget_exceeded","source list exceeds the 64 MiB inspection budget")
                        files.append({"path":rel,"byte_length":len(data),"sha256":_digest(data)})
                return {"ok":True,"workspace_revision":revision,"files":files}
            if query["query"] == "source_text":
                if set(query)!={"query","path"}: _fail("invalid_request","source_text inspect requires query and path")
                rel,target=_resolve_owned(config,query.get("path"))
                data=_read_source(config,rel)
                assert data is not None
                if len(data)>_MAX_TEXT_BYTES: _fail("budget_exceeded","source text exceeds 32 KiB")
                if _contains_secret(data): _fail("permission_denied","source text matched a secret-like pattern")
                try: text=data.decode("utf-8",errors="strict")
                except UnicodeDecodeError: _fail("permission_denied","source text is not valid UTF-8")
                return {"ok":True,"path":rel,"content_utf8":text,"sha256":_digest(data),"workspace_revision":revision}
            _fail("unsupported_capability","inspect query is not available in slice A")
    except (WorkspaceError,WorkspaceFileError,ContractError) as exc:
        return _error(exc)


def _write_new(path: Path, data: bytes) -> None:
    _ensure_dir(path.parent)
    try:
        with exclusive_file(path,create=True) as handle:
            handle.write_bytes(data)
    except WorkspaceFileError as exc:
        raise WorkspaceError(exc.code,str(exc)) from exc


def apply_patch(scope: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    before_path: Path | None = None
    try:
        config=_normalize_scope(scope)
        if type(request) is not dict or set(request)!={"operation_id","path","expected_before_hash","content_utf8"}:
            _fail("invalid_request","patch request fields are invalid")
        operation_id=request["operation_id"]
        if not isinstance(operation_id,str) or not _UUID.fullmatch(operation_id): _fail("invalid_request","operation_id must be a UUID")
        rel,target=_resolve_owned(config,request["path"])
        expected=request["expected_before_hash"]
        if not isinstance(expected,str) or expected!="missing" and not re.fullmatch(r"sha256:[0-9a-f]{64}",expected): _fail("invalid_request","expected_before_hash is invalid")
        text=request["content_utf8"]
        if not isinstance(text,str): _fail("invalid_request","content_utf8 must be a string")
        try: new_bytes=text.encode("utf-8",errors="strict")
        except UnicodeEncodeError: _fail("invalid_request","content contains an unpaired surrogate")
        if len(new_bytes)>_MAX_TEXT_BYTES: _fail("budget_exceeded","patch content exceeds 32 KiB")
        if _contains_secret(new_bytes): _fail("permission_denied","patch content contains a secret-like value")
        if len(canonical_json(request))>_MAX_CONTROL_BYTES: _fail("budget_exceeded","patch request exceeds 64 KiB")
        before_dir=config["state_root"] / "before-images"
        _ensure_dir(before_dir)
        meta_path=before_dir / f"{operation_id}.json"
        data_path=before_dir / f"{operation_id}.bin"
        if meta_path.exists() or data_path.exists(): _fail("identity_mismatch","operation id already has a before image")
        with _workspace_lease(config) as (lease,state):
            state=_observe_workspace(config,lease,state)
            if expected=="missing":
                if target.exists(): _fail("scope_conflict","new-file target already exists")
                metadata={"operation_id":operation_id,"path":rel,"existed":False,"before_hash":"missing"}
                _write_new(meta_path,canonical_json(metadata))
                with exclusive_file(target,create=True) as target_handle:
                    target_handle.write_bytes(new_bytes)
            else:
                try:
                    with exclusive_file(target,create=False) as target_handle:
                        info=os.stat(target,follow_symlinks=False)
                        if not os.path.isfile(target) or getattr(info,"st_file_attributes",0)&0x400: _fail("scope_conflict","target is not a regular file")
                        before=target_handle.read_bytes(max_bytes=_MAX_SOURCE_FILE_BYTES)
                        actual=_digest(before)
                        if actual!=expected: _fail("scope_conflict","external edit changed the expected before hash")
                        if _contains_secret(before): _fail("permission_denied","before image contains a secret-like value")
                        _write_new(data_path,before)
                        metadata={"operation_id":operation_id,"path":rel,"existed":True,"before_hash":actual,"byte_length":len(before)}
                        _write_new(meta_path,canonical_json(metadata))
                        target_handle.write_bytes(new_bytes)
                except WorkspaceFileError as exc:
                    raise WorkspaceError(exc.code,str(exc)) from exc
            new_state=_observe_workspace(config,lease,state)
            new_revision=new_state["workspace_revision"]
        before_path=data_path if expected!="missing" else meta_path
        return {"ok":True,"path":rel,"before_hash":expected,"after_hash":_digest(new_bytes),"workspace_revision":new_revision,"before_image_path":str(before_path),"before_image_manifest":str(meta_path)}
    except (WorkspaceError,WorkspaceFileError,ContractError) as exc:
        return _error(exc)
    except OSError as exc:
        return _error(WorkspaceError("outcome_unknown", "patch I/O failed; inspect the recorded before image before retrying"))


def capture_snapshot(scope: dict[str, Any], include_paths: list[str], output_root: Path | None = None, *, expected_revision: int | None = None) -> dict[str, Any]:
    try:
        config=_normalize_scope(scope)
        if type(include_paths) is not list or not 1<=len(include_paths)<=_MAX_FILES: _fail("invalid_request","include_paths must contain 1..256 files")
        paths=[_safe_relative(item) for item in include_paths]
        if len({p.lower() for p in paths})!=len(paths): _fail("invalid_request","include_paths contains duplicate/case-colliding paths")
        if any(p.lower() not in config["owned_casefold"] for p in paths): _fail("permission_denied","snapshot path is outside owned_paths")
        paths.sort(key=lambda p:p.lower())
        destination_root=config["state_root"] / "snapshots" if output_root is None else Path(output_root)
        if not destination_root.is_absolute(): _fail("scope_conflict","snapshot output root must be absolute")
        destination_root=Path(os.path.abspath(destination_root))
        if os.path.commonpath((os.path.normcase(str(config["state_root"])),os.path.normcase(str(destination_root))))!=os.path.normcase(str(config["state_root"])):_fail("permission_denied","snapshot output must be inside state_root")
        with _workspace_lease(config) as (lease,state):
            state=_observe_workspace(config,lease,state)
            revision=state["workspace_revision"]
            if expected_revision is not None and (type(expected_revision) is not int or expected_revision!=revision): _fail("scope_conflict","workspace revision changed before snapshot")
            first={}
            total=0
            for rel in paths:
                data=_read_source(config,rel)
                assert data is not None
                total+=len(data)
                if total>_MAX_TOTAL_SNAPSHOT_BYTES:_fail("budget_exceeded","snapshot exceeds 64 MiB")
                if _contains_secret(data):_fail("permission_denied","snapshot contains a secret-like value")
                first[rel]=data
            manifest={"format":"evolution-source-snapshot/1","scope_id":config["scope_id"],"workspace_revision":revision,"exclusion_policy":"evolution-sandbox-v1","case_policy":"windows-case-insensitive-v1","files":[{"path":rel,"byte_length":len(first[rel]),"sha256":_digest(first[rel])} for rel in paths]}
            snapshot_id=content_hash(manifest)
            _ensure_dir(destination_root)
            final_root=destination_root / snapshot_id.removeprefix("sha256:")
            if final_root.exists():
                if _has_reparse_component(final_root,include_final=True) or not final_root.is_dir(): _fail("scope_conflict","existing snapshot destination is not a real directory")
                manifest_path=final_root/"manifest.json"
                if _has_reparse_component(manifest_path,include_final=True) or not manifest_path.is_file() or manifest_path.read_bytes()!=canonical_json(manifest): _fail("recovery_required","existing snapshot manifest does not match its content identity")
                tree=final_root/"tree"
                if _has_reparse_component(tree,include_final=True) or not tree.is_dir(): _fail("recovery_required","existing snapshot tree is not a real directory")
                actual=[]
                for rel in paths:
                    file=tree.joinpath(*rel.split("/"))
                    if _has_reparse_component(file,include_final=True) or not file.is_file(): _fail("recovery_required","existing snapshot tree is incomplete or contains a reparse point")
                    data=file.read_bytes()
                    if data!=first[rel]: _fail("recovery_required","existing immutable snapshot bytes changed")
                    actual.append(rel)
                actual_files=[]
                for directory,dirs,files in os.walk(tree,followlinks=False):
                    current=Path(directory)
                    for name in dirs:
                        entry=current/name
                        if _has_reparse_component(entry,include_final=True): _fail("recovery_required","existing snapshot contains a reparse directory")
                    for name in files:
                        entry=current/name
                        if _has_reparse_component(entry,include_final=True): _fail("recovery_required","existing snapshot contains a reparse file")
                        actual_files.append(entry.relative_to(tree).as_posix())
                if sorted(actual_files)!=sorted(paths):_fail("recovery_required","existing snapshot contains missing or unowned files")
                return {"ok":True,"source_snapshot_id":snapshot_id,"workspace_revision":revision,"manifest":manifest,"manifest_path":str(manifest_path),"tree_root":str(tree),"byte_length":total}
            stage=destination_root / f".{snapshot_id.removeprefix('sha256:')}.{uuid.uuid4().hex}.partial"
            stage.mkdir(parents=False,exist_ok=False)
            tree=stage/"tree";tree.mkdir()
            for rel,data in first.items():
                target=tree.joinpath(*rel.split("/"))
                _ensure_dir(target.parent)
                with open(target,"xb") as stream:
                    stream.write(data);stream.flush();os.fsync(stream.fileno())
            manifest_path=stage/"manifest.json"
            with open(manifest_path,"xb") as stream:
                stream.write(canonical_json(manifest));stream.flush();os.fsync(stream.fileno())
            for rel in paths:
                second=_read_source(config,rel)
                if second!=first[rel]:
                    _observe_workspace(config,lease,state)
                    _fail("scope_conflict","source changed while snapshot was being captured")
            after_state=_observe_workspace(config,lease,state)
            if after_state["workspace_revision"]!=revision:
                _fail("scope_conflict","source changed while snapshot was being captured")
            os.rename(stage,final_root)
        return {"ok":True,"source_snapshot_id":snapshot_id,"workspace_revision":revision,"manifest":manifest,"manifest_path":str(final_root/"manifest.json"),"tree_root":str(final_root/"tree"),"byte_length":total}
    except (WorkspaceError,WorkspaceFileError,ContractError) as exc:
        return _error(exc)
    except OSError:
        return _error(WorkspaceError("scope_conflict","snapshot I/O failed; partial artifacts were preserved"))
