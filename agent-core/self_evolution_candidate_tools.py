"""Host-injected filesystem and verification tools for the self-evolution worker.

This module is not an OS sandbox. The trusted host must resolve only an isolated
candidate (never the stable tree) and validate its live task/attempt binding on
every call. Filesystem checks reject observed links/reparse points, but Python's
portable path APIs cannot close Windows reparse-point swap races atomically.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence

from self_evolution_verification import VerificationRunner
from self_evolution_worker import HostTool, SelfEvolutionWorkerContext

_MAX_READ_BYTES = 4 * 1024 * 1024
_MAX_WRITE_BYTES = 4 * 1024 * 1024
_MAX_PENDING_WRITE_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 8 * 1024
_MAX_PENDING_WRITES = 4
_MAX_WRITE_CHUNK_ENCODED = 12 * 1024
_MAX_INVENTORY_FILES = 100_000
_MAX_INVENTORY_RESPONSE = 12 * 1024
_MAX_DIRECTORY_ENTRIES = 100_000
_MAX_VISITED_DIRECTORIES = 10_000
_OBSERVE_PAGE_SIZE = 128
_MAX_CURSOR_CHARS = 256
_MAX_RELATIVE_PATH = 1024
_MAX_CHECK_ID = 100
_SENSITIVE_COMPONENTS = {".git", ".ssh", ".aws", ".azure", ".config", "secrets", "credentials"}
_SENSITIVE_NAME = re.compile(r"(^\.env(?:\.|$)|credential|secret|private[_-]?key|id_rsa|id_ed25519|\.(?:pem|p12|pfx|key)$)", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?[A-Z0-9_.-]*(?:api[_-]?key|access[_-]?(?:token|key)|auth[_-]?token|client[_-]?secret|private[_-]?key|password|passwd|secret|token)[A-Z0-9_.-]*[\"']?\s*[:=]\s*)([\"']?)([^\"'\r\n,;]+)\2"
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
_SECRET_MARKERS = ("api", "access", "auth", "client", "private", "password", "passwd", "secret", "token", "credential")
_MAX_SECRET_SCAN_LINE_CHARS = 2 * 1024


def _redact_content(content: str) -> str:
    """Redact secret patterns without applying backtracking regexes to huge lines."""
    if "PRIVATE KEY" in content.upper():
        content = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", content)
    if "bearer" in content.casefold():
        content = _BEARER.sub(r"\1[REDACTED]", content)
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        folded = line.casefold()
        if any(marker in folded for marker in _SECRET_MARKERS):
            if len(line) > _MAX_SECRET_SCAN_LINE_CHARS:
                ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
                line = "[REDACTED LONG LINE WITH SENSITIVE MARKER]" + ending
            else:
                line = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]\2", line)
        lines.append(line)
    return "".join(lines)


class CandidateToolError(PermissionError):
    """Sanitized, model-safe failure. Never includes a host path or file content."""


def _replace_existing_windows(target: Path, replacement: str, backup: str) -> None:
    """Replace an existing file without suppressing attribute or DACL merge errors."""
    import ctypes
    from ctypes import wintypes

    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    if not replace(str(target), replacement, backup, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def build_self_evolution_candidate_tools(
    *,
    resolve_candidate_root: Callable[[SelfEvolutionWorkerContext], str | os.PathLike[str] | None],
    validate_binding: Callable[[SelfEvolutionWorkerContext, Path], bool],
    verification_runner: VerificationRunner,
    registered_check_ids: Sequence[str] | set[str] | frozenset[str],
    check_timeout_seconds: float = 120.0,
    include_verification: bool = True,
) -> tuple[HostTool, ...]:
    """Build fixed worker tools from trusted host capabilities.

    ``resolve_candidate_root`` and ``validate_binding`` are trusted host code.
    The validator must authenticate the current task, attempt, candidate and
    root and must reject the stable repository. Check IDs must match the IDs
    registered in the injected runner. Model arguments never carry roots,
    commands, credentials, or environment configuration.
    """
    if not callable(resolve_candidate_root) or not callable(validate_binding):
        raise ValueError("trusted candidate resolver and binding validator required")
    if not isinstance(verification_runner, VerificationRunner):
        raise ValueError("trusted VerificationRunner instance required")
    if not isinstance(registered_check_ids, (Sequence, set, frozenset)) or isinstance(registered_check_ids, (str, bytes)):
        raise ValueError("registered check IDs must be a finite collection")
    if type(include_verification) is not bool:
        raise ValueError("include_verification must be a boolean")
    check_ids = frozenset(registered_check_ids)
    cursor_key = secrets.token_bytes(32)
    pending_writes: dict[tuple[str, ...], dict[str, Any]] = {}
    if (include_verification and not check_ids) or any(
        type(item) is not str or not item or len(item) > _MAX_CHECK_ID
        or item != item.strip() or "\x00" in item for item in check_ids
    ):
        raise ValueError("invalid registered check IDs")
    if type(check_timeout_seconds) not in (int, float) or not 0 < check_timeout_seconds <= 3600:
        raise ValueError("check timeout out of range")

    def _binding(context: SelfEvolutionWorkerContext) -> Path:
        try:
            raw_root = resolve_candidate_root(context)
            if raw_root is None:
                raise CandidateToolError("candidate_unavailable")
            root = Path(os.fspath(raw_root))
            if not root.is_absolute():
                raise CandidateToolError("candidate_unavailable")
            # Reject a linked/reparse candidate root itself. Ancestor/root trust is
            # still the resolver's responsibility; never report the path on error.
            root_stat = root.lstat()
            if _is_link_or_reparse(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
                raise CandidateToolError("candidate_unavailable")
            resolved = root.resolve(strict=True)
            if not _validate(context, resolved):
                raise CandidateToolError("candidate_binding_invalid")
            return resolved
        except CandidateToolError:
            raise
        except Exception:
            raise CandidateToolError("candidate_unavailable") from None

    def _validate(context: SelfEvolutionWorkerContext, root: Path) -> bool:
        try:
            return validate_binding(context, root) is True
        except Exception:
            return False

    def _revalidate(context: SelfEvolutionWorkerContext, root: Path) -> None:
        current = _binding(context)
        if not _same_path(current, root):
            raise CandidateToolError("candidate_binding_changed")

    def _relative(raw: object) -> tuple[str, ...]:
        if type(raw) is not str or not raw or len(raw) > _MAX_RELATIVE_PATH or "\x00" in raw:
            raise CandidateToolError("path_invalid")
        if raw.startswith(("/", "\\")) or PureWindowsPath(raw).drive or PureWindowsPath(raw).root:
            raise CandidateToolError("path_invalid")
        # Accept slash-separated relative paths only, consistently on all hosts.
        if "\\" in raw:
            raise CandidateToolError("path_invalid")
        parts = tuple(raw.split("/"))
        if any(part in {"", ".", ".."} for part in parts):
            raise CandidateToolError("path_invalid")
        if any(":" in part for part in parts):
            raise CandidateToolError("path_invalid")
        if any(part.casefold() in _SENSITIVE_COMPONENTS or _SENSITIVE_NAME.search(part) for part in parts):
            raise CandidateToolError("path_sensitive")
        return parts

    def _path(root: Path, parts: tuple[str, ...], *, create_parents: bool = False) -> Path:
        current = root
        for part in parts[:-1]:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not create_parents:
                    raise CandidateToolError("path_not_found") from None
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                info = current.lstat()
            except OSError:
                raise CandidateToolError("filesystem_unavailable") from None
            if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise CandidateToolError("path_invalid")
        target = current / parts[-1]
        try:
            info = target.lstat()
        except FileNotFoundError:
            return target
        except OSError:
            raise CandidateToolError("filesystem_unavailable") from None
        if (_is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode)
                or getattr(info, "st_nlink", 1) > 1):
            raise CandidateToolError("path_invalid")
        return target

    def _cursor_scope(context: SelfEvolutionWorkerContext, root: Path, directory: str,
                      inventory_digest: str) -> str:
        identity = json.dumps((context.session_id, context.task_id, context.attempt_id,
                               context.run_id, context.candidate_id, str(root), directory,
                               inventory_digest), separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _encode_cursor(offset: int, scope: str) -> str:
        payload = json.dumps({"offset": offset, "scope": scope}, separators=(",", ":")).encode("ascii")
        signature = hmac.new(cursor_key, payload, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=") + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")
        return encoded.decode("ascii")

    def _decode_cursor(cursor: object, scope: str, total: int) -> int:
        if type(cursor) is not str or not cursor or len(cursor) > _MAX_CURSOR_CHARS or cursor.count(".") != 1:
            raise CandidateToolError("cursor_invalid")
        payload_text, signature_text = cursor.split(".", 1)
        try:
            payload = base64.b64decode(payload_text + "=" * (-len(payload_text) % 4), altchars=b"-_", validate=True)
            signature = base64.b64decode(signature_text + "=" * (-len(signature_text) % 4), altchars=b"-_", validate=True)
            expected = hmac.new(cursor_key, payload, hashlib.sha256).digest()
            decoded = json.loads(payload.decode("ascii"))
        except (ValueError, UnicodeError, json.JSONDecodeError):
            raise CandidateToolError("cursor_invalid") from None
        if (not hmac.compare_digest(signature, expected) or type(decoded) is not dict
                or set(decoded) != {"offset", "scope"} or type(decoded.get("offset")) is not int
                or decoded["scope"] != scope or not 0 <= decoded["offset"] < total):
            raise CandidateToolError("cursor_invalid")
        return decoded["offset"]

    def observe(context: SelfEvolutionWorkerContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        root = _binding(context)
        raw_directory = arguments.get("directory")
        if raw_directory is None:
            directory_parts: tuple[str, ...] = ()
            directory_label = ""
            base = root
        else:
            directory_parts = _relative(raw_directory)
            directory_label = "/".join(directory_parts)
            base = root
            for part in directory_parts:
                base = base / part
                try:
                    info = base.lstat()
                except OSError:
                    raise CandidateToolError("directory_unavailable") from None
                if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                    raise CandidateToolError("directory_unavailable")

        # Collect metadata only; file contents and aggregate file sizes do not
        # participate in inventory eligibility or pagination.
        entries: list[tuple[str, int, int, int]] = []
        stack: list[tuple[Path, tuple[str, ...]]] = [(base, directory_parts)]
        visited_directories = 0
        while stack:
            visited_directories += 1
            if visited_directories > _MAX_VISITED_DIRECTORIES:
                raise CandidateToolError("inventory_limit_reached")
            directory, prefix = stack.pop()
            _revalidate(context, root)
            try:
                children = []
                with os.scandir(directory) as iterator:
                    for index, entry in enumerate(iterator):
                        if index >= _MAX_DIRECTORY_ENTRIES:
                            raise CandidateToolError("inventory_limit_reached")
                        children.append(entry)
                children.sort(key=lambda entry: (entry.name.casefold(), entry.name), reverse=True)
            except CandidateToolError:
                raise
            except OSError:
                raise CandidateToolError("filesystem_unavailable") from None
            for entry in children:
                name = entry.name
                if name.casefold() in _SENSITIVE_COMPONENTS or _SENSITIVE_NAME.search(name):
                    continue
                if "\\" in name or any(ord(char) < 32 for char in name):
                    continue
                rel_parts = prefix + (name,)
                rel = "/".join(rel_parts)
                if len(rel) > _MAX_RELATIVE_PATH:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    raise CandidateToolError("filesystem_unavailable") from None
                if _is_link_or_reparse(info):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    stack.append((Path(entry.path), rel_parts))
                elif stat.S_ISREG(info.st_mode):
                    entries.append((rel, info.st_size, getattr(info, "st_mtime_ns", 0), info.st_ino))
                    if len(entries) > _MAX_INVENTORY_FILES:
                        raise CandidateToolError("inventory_limit_reached")

        _revalidate(context, root)
        entries.sort(key=lambda item: (item[0].casefold(), item[0]))
        inventory_digest = hashlib.sha256(json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        scope = _cursor_scope(context, root, directory_label, inventory_digest)
        cursor = arguments.get("cursor")
        offset = 0 if cursor is None else _decode_cursor(cursor, scope, len(entries))
        page: list[dict[str, Any]] = []
        for rel, size, _mtime, _inode in entries[offset:offset + _OBSERVE_PAGE_SIZE]:
            item = {"path": rel, "size_bytes": size}
            page.append(item)
            probe = {"status": "observed", "files": page,
                     "next_cursor": "x" * _MAX_CURSOR_CHARS, "has_more": True,
                     "truncated": False, "evidence_ref": "x" * 64}
            if len(json.dumps(probe, ensure_ascii=False, separators=(",", ":"))) > _MAX_INVENTORY_RESPONSE:
                page.pop()
                break
        if not page and offset < len(entries):
            raise CandidateToolError("inventory_item_too_large")
        next_offset = offset + len(page)
        has_more = next_offset < len(entries)
        next_cursor = _encode_cursor(next_offset, scope) if has_more else None
        result = {
            "status": "observed", "files": page, "next_cursor": next_cursor,
            "has_more": has_more, "truncated": False,
            "summary": f"Listed {len(page)} of {len(entries)} candidate files" + ("; continue with next_cursor" if has_more else ""),
            "evidence_ref": _evidence_ref("observation"),
        }
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > _MAX_INVENTORY_RESPONSE:
            raise CandidateToolError("observation_response_too_large")
        return result

    def read_file(context: SelfEvolutionWorkerContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_path = arguments.get("path")
        offset = 0
        expected_digest: str | None = None
        find_query: str | None = None
        if type(raw_path) is str and raw_path.startswith("@find:"):
            match = re.fullmatch(r"@find:([A-Za-z0-9_-]+):(.+)", raw_path)
            if match is None:
                raise CandidateToolError("find_query_invalid")
            encoded_query, raw_path = match.groups()
            if len(encoded_query) > 684:
                raise CandidateToolError("find_query_too_large")
            try:
                query_bytes = base64.b64decode(encoded_query + "=" * (-len(encoded_query) % 4),
                                               altchars=b"-_", validate=True)
                find_query = query_bytes.decode("utf-8")
            except (ValueError, UnicodeError):
                raise CandidateToolError("find_query_invalid") from None
            if not query_bytes or len(query_bytes) > 512 or not find_query.strip() or any(ord(c) < 32 for c in find_query):
                raise CandidateToolError("find_query_invalid")
        elif type(raw_path) is str and raw_path.startswith("@read:"):
            match = re.fullmatch(r"@read:(0|[1-9][0-9]*):(-|[0-9a-f]{64}):(.+)", raw_path)
            if match is None:
                raise CandidateToolError("read_cursor_invalid")
            offset = int(match.group(1))
            expected_digest = None if match.group(2) == "-" else match.group(2)
            if (offset == 0) != (expected_digest is None):
                raise CandidateToolError("read_cursor_invalid")
            raw_path = match.group(3)
        parts = _relative(raw_path)
        root = _binding(context)
        target = _path(root, parts)
        try:
            before = target.lstat()
            if (_is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode)
                    or getattr(before, "st_nlink", 1) > 1 or before.st_size > _MAX_READ_BYTES):
                raise CandidateToolError("file_unavailable")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(target, flags)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
                    raise CandidateToolError("file_changed")
                data = bytearray()
                remaining = _MAX_READ_BYTES + 1
                while remaining:
                    chunk = os.read(fd, min(64 * 1024, remaining))
                    if not chunk:
                        break
                    data.extend(chunk)
                    remaining -= len(chunk)
                if len(data) > _MAX_READ_BYTES:
                    raise CandidateToolError("file_too_large")
            finally:
                os.close(fd)
            after = target.lstat()
            if _is_link_or_reparse(after) or _identity(after) != _identity(opened):
                raise CandidateToolError("file_changed")
            _revalidate(context, root)
            digest = hashlib.sha256(data).hexdigest()
            if expected_digest is not None and not hmac.compare_digest(expected_digest, digest):
                raise CandidateToolError("file_changed")
            content = data.decode("utf-8")
            del data
            content = _redact_content(content)
            content = "".join(char if char in "\n\r\t" or ord(char) >= 32 else "?" for char in content)
            if find_query is not None:
                found: list[dict[str, Any]] = []
                match_count = content.count(find_query)
                search_from = 0
                context_chars = 240
                while len(found) < min(5, match_count):
                    match_at = content.find(find_query, search_from)
                    if match_at < 0:
                        break
                    left = max(0, match_at - context_chars)
                    right = min(len(content), match_at + len(find_query) + context_chars)
                    found.append({"sanitized_offset": match_at, "context": content[left:right]})
                    search_from = match_at + max(1, len(find_query))
                payload = {
                    "status": "found" if match_count else "not_found", "matches": found,
                    "match_count": match_count, "multiple_matches": match_count > 1,
                    "truncated": match_count > len(found), "sha256": digest,
                    "evidence_ref": _evidence_ref("find"),
                }
                if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) > 14 * 1024:
                    raise CandidateToolError("find_response_too_large")
                return payload
            sanitized = content.encode("utf-8")
            if offset > len(sanitized):
                raise CandidateToolError("read_cursor_invalid")
            if offset < len(sanitized) and offset and (sanitized[offset] & 0xC0) == 0x80:
                raise CandidateToolError("read_cursor_invalid")
            end = min(offset + _READ_CHUNK_BYTES, len(sanitized))
            while True:
                while end < len(sanitized) and end > offset and (sanitized[end] & 0xC0) == 0x80:
                    end -= 1
                segment = sanitized[offset:end].decode("utf-8")
                payload = {
                    "status": "ok", "content": segment, "offset": offset, "next_offset": end,
                    "complete": end == len(sanitized), "sha256": digest,
                    "evidence_ref": _evidence_ref("read"),
                }
                if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 14 * 1024:
                    break
                if end <= offset:
                    raise CandidateToolError("read_chunk_too_large")
                end = max(offset, end - 512)
        except CandidateToolError:
            raise
        except UnicodeDecodeError:
            raise CandidateToolError("file_encoding_unsupported") from None
        except OSError:
            raise CandidateToolError("file_unavailable") from None
        return payload

    def write_file(context: SelfEvolutionWorkerContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        parts = _relative(arguments.get("path"))
        content = arguments.get("content")
        if type(content) is not str:
            raise CandidateToolError("content_invalid")
        root = _binding(context)
        target = _path(root, parts, create_parents=True)
        envelope: Any = None
        if content.lstrip().startswith("{"):
            if len(content.encode("utf-8")) > 16 * 1024:
                raise CandidateToolError("tool_envelope_too_large")
            try:
                parsed_content = json.loads(content)
            except (ValueError, json.JSONDecodeError):
                if "$candidate_write_chunk" in content[:256]:
                    raise CandidateToolError("chunk_invalid") from None
                parsed_content = None
            if isinstance(parsed_content, dict) and "$candidate_write_chunk" in parsed_content:
                envelope = parsed_content
            elif isinstance(parsed_content, dict) and "$candidate_edit" in parsed_content:
                envelope = parsed_content
        if isinstance(envelope, dict) and "$candidate_edit" in envelope:
            if len(content) > 16 * 1024:
                raise CandidateToolError("edit_envelope_too_large")
            return _apply_exact_edit(context, root, target, parts, envelope)
        if envelope is None:
            data = content.encode("utf-8")
            if len(data) > _MAX_WRITE_BYTES:
                raise CandidateToolError("content_too_large")
            baseline = _target_snapshot(context, root, target, parts)
            return _commit_write(context, root, target, parts, data, expected_target_snapshot=baseline)

        if (len(content) > 16 * 1024 or type(envelope) is not dict or set(envelope) != {
                "$candidate_write_chunk", "write_id", "index", "total_bytes", "sha256", "data"}
                or envelope.get("$candidate_write_chunk") != 1):
            raise CandidateToolError("chunk_invalid")
        write_id, index = envelope.get("write_id"), envelope.get("index")
        total_bytes, digest, encoded_data = envelope.get("total_bytes"), envelope.get("sha256"), envelope.get("data")
        if (type(write_id) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", write_id)
                or type(index) is not int or index < 0
                or type(total_bytes) is not int or not 0 < total_bytes <= _MAX_WRITE_BYTES
                or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or type(encoded_data) is not str or len(encoded_data) > _MAX_WRITE_CHUNK_ENCODED):
            raise CandidateToolError("chunk_invalid")
        try:
            chunk = base64.b64decode(encoded_data, validate=True)
        except (ValueError, TypeError):
            raise CandidateToolError("chunk_invalid") from None
        if not chunk or len(chunk) > 9 * 1024:
            raise CandidateToolError("chunk_invalid")
        now = time.monotonic()
        for stale_key, stale in list(pending_writes.items()):
            if now - stale.get("updated_at", now) > 600:
                pending_writes.pop(stale_key, None)
        key = (context.session_id, context.task_id, context.attempt_id, context.run_id,
               context.candidate_id, str(root), "/".join(parts), write_id)
        pending = pending_writes.get(key)
        if index == 0:
            if pending is not None:
                raise CandidateToolError("chunk_replay")
            if len(pending_writes) >= _MAX_PENDING_WRITES:
                raise CandidateToolError("chunk_capacity_reached")
            if sum(item["total_bytes"] for item in pending_writes.values()) + total_bytes > _MAX_PENDING_WRITE_BYTES:
                raise CandidateToolError("chunk_memory_limit_reached")
            baseline = _target_snapshot(context, root, target, parts)
            pending = {"next_index": 0, "total_bytes": total_bytes, "sha256": digest,
                       "data": bytearray(), "target_snapshot": baseline, "updated_at": now}
            pending_writes[key] = pending
        if (pending is None or index != pending["next_index"]
                or total_bytes != pending["total_bytes"] or digest != pending["sha256"]):
            raise CandidateToolError("chunk_order_invalid")
        if len(pending["data"]) + len(chunk) > total_bytes:
            pending_writes.pop(key, None)
            raise CandidateToolError("chunk_size_invalid")
        pending["data"].extend(chunk)
        pending["next_index"] += 1
        pending["updated_at"] = now
        if len(pending["data"]) < total_bytes:
            if len(pending["data"]) + 1 > total_bytes:
                pending_writes.pop(key, None)
                raise CandidateToolError("chunk_size_invalid")
            return {"status": "ok", "chunk_status": "accepted", "next_index": pending["next_index"],
                    "received_bytes": len(pending["data"]), "sha256": digest,
                    "evidence_ref": _evidence_ref("write-chunk")}
        data = bytes(pending["data"])
        pending_writes.pop(key, None)
        if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest):
            raise CandidateToolError("content_digest_mismatch")
        return _commit_write(context, root, target, parts, data, expected_digest=digest,
                             expected_target_snapshot=pending["target_snapshot"])

    def _apply_exact_edit(context: SelfEvolutionWorkerContext, root: Path, target: Path,
                          parts: tuple[str, ...], envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        if (set(envelope) != {"$candidate_edit", "base_sha256", "old", "new"}
                or envelope.get("$candidate_edit") != 1):
            raise CandidateToolError("edit_envelope_invalid")
        base_digest, old, new = envelope.get("base_sha256"), envelope.get("old"), envelope.get("new")
        if (type(base_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", base_digest)
                or type(old) is not str or not old or type(new) is not str or not new):
            raise CandidateToolError("edit_envelope_invalid")
        try:
            before = target.lstat()
            if (_is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode)
                    or getattr(before, "st_nlink", 1) > 1 or before.st_size > _MAX_WRITE_BYTES):
                raise CandidateToolError("file_unavailable")
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
                    raise CandidateToolError("file_changed")
                data = bytearray()
                remaining = _MAX_WRITE_BYTES + 1
                while remaining:
                    chunk = os.read(fd, min(64 * 1024, remaining))
                    if not chunk:
                        break
                    data.extend(chunk)
                    remaining -= len(chunk)
                if len(data) > _MAX_WRITE_BYTES:
                    raise CandidateToolError("file_too_large")
            finally:
                os.close(fd)
            after = target.lstat()
            if _is_link_or_reparse(after) or _identity(after) != _identity(opened):
                raise CandidateToolError("file_changed")
            _revalidate(context, root)
            actual_base = hashlib.sha256(data).hexdigest()
            if not hmac.compare_digest(actual_base, base_digest):
                raise CandidateToolError("base_digest_mismatch")
            text = data.decode("utf-8")
            del data
        except CandidateToolError:
            raise
        except UnicodeDecodeError:
            raise CandidateToolError("file_encoding_unsupported") from None
        except OSError:
            raise CandidateToolError("file_unavailable") from None
        if text.count(old) != 1:
            raise CandidateToolError("edit_target_not_unique")
        updated = text.replace(old, new, 1).encode("utf-8")
        if len(updated) > _MAX_WRITE_BYTES:
            raise CandidateToolError("content_too_large")
        return _commit_write(context, root, target, parts, updated,
                             expected_digest=hashlib.sha256(updated).hexdigest(),
                             expected_target_snapshot=(_identity(opened), actual_base))

    def _target_snapshot(context: SelfEvolutionWorkerContext, root: Path, target: Path,
                         parts: tuple[str, ...]) -> tuple[tuple[int, ...] | None, str | None]:
        # Hash in bounded chunks, so a timestamp-preserving content change is detected.
        _revalidate(context, root)
        current = _path(root, parts)
        if not _same_path(current, target):
            raise CandidateToolError("candidate_binding_changed")
        try:
            before = current.lstat()
        except FileNotFoundError:
            return (None, None)
        except OSError:
            raise CandidateToolError("file_unavailable") from None
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(current, flags)
            try:
                opened = os.fstat(fd)
                if (not stat.S_ISREG(opened.st_mode) or getattr(opened, "st_nlink", 1) > 1
                        or _identity(opened) != _identity(before)):
                    raise CandidateToolError("file_changed")
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(fd, 64 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                if _identity(os.fstat(fd)) != _identity(opened):
                    raise CandidateToolError("file_changed")
            finally:
                os.close(fd)
            after = current.lstat()
            if (_is_link_or_reparse(after) or getattr(after, "st_nlink", 1) > 1
                    or _identity(after) != _identity(opened)):
                raise CandidateToolError("file_changed")
            _revalidate(context, root)
            return (_identity(opened), digest.hexdigest())
        except CandidateToolError:
            raise
        except OSError:
            raise CandidateToolError("file_changed") from None

    def _commit_write(context: SelfEvolutionWorkerContext, root: Path, target: Path,
                      parts: tuple[str, ...], data: bytes | bytearray, *,
                      expected_target_snapshot: tuple[tuple[int, ...] | None, str | None],
                      expected_digest: str | None = None) -> Mapping[str, Any]:
        created = expected_target_snapshot[0] is None
        temp_name: str | None = None
        backup_name: str | None = None
        preserve_recovery = False
        try:
            fd, temp_name = tempfile.mkstemp(prefix=".worker-write-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            _revalidate(context, root)
            current_target = _path(root, parts)
            if not _same_path(current_target, target):
                raise CandidateToolError("candidate_binding_changed")
            if expected_target_snapshot[0] is not None:
                # Preserve portable mode bits; ReplaceFileW separately merges
                # the existing file's DACL on Windows without ignoring errors.
                os.chmod(temp_name, stat.S_IMODE(expected_target_snapshot[0][2]))
            if _target_snapshot(context, root, target, parts) != expected_target_snapshot:
                raise CandidateToolError("file_changed")
            # The snapshot and replace are not an atomic compare-and-swap; hostile
            # concurrent filesystem changes can still race this final step.
            if os.name == "nt" and not created:
                backup_name = temp_name + ".backup"
                if os.path.lexists(backup_name):
                    raise CandidateToolError("backup_path_unavailable")
                # Certain ReplaceFileW failure modes move the original or the
                # replacement. Keep both recovery files when the call fails.
                preserve_recovery = True
                _replace_existing_windows(target, temp_name, backup_name)
                preserve_recovery = False
            else:
                os.replace(temp_name, target)
            temp_name = None
            _revalidate(context, root)
            info = target.lstat()
            if (_is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode)
                    or info.st_size != len(data)):
                raise CandidateToolError("write_unconfirmed")
            actual_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            expected = expected_digest or hashlib.sha256(data).hexdigest()
            if not hmac.compare_digest(actual_digest, expected):
                raise CandidateToolError("write_unconfirmed")
            if backup_name:
                os.unlink(backup_name)
                backup_name = None
            return {"status": "written", "created": created, "readback_verified": True,
                    "sha256": actual_digest, "bytes_written": len(data),
                    "evidence_ref": _evidence_ref("write")}
        except CandidateToolError:
            raise
        except OSError:
            if preserve_recovery or backup_name:
                raise CandidateToolError("write_failed_recovery_required") from None
            raise CandidateToolError("write_failed") from None
        finally:
            if temp_name and not preserve_recovery:
                try:
                    if os.name == "nt":
                        os.chmod(temp_name, stat.S_IWRITE | stat.S_IREAD)
                    os.unlink(temp_name)
                except OSError:
                    pass

    def run_check(context: SelfEvolutionWorkerContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        check_id = arguments.get("check_id")
        if type(check_id) is not str or check_id not in check_ids:
            raise CandidateToolError("check_not_registered")
        root = _binding(context)
        try:
            result = verification_runner.run(
                task_id=context.task_id,
                command_id=check_id,
                timeout_seconds=float(check_timeout_seconds),
                cancel_event=context.cancel_event,
            )
        except Exception:
            # Runner exceptions do not constitute evidence of a pass.
            result = None
        try:
            _revalidate(context, root)
        except CandidateToolError:
            return {"status": "unknown", "check_id": check_id,
                    "candidate_digest": _current_digest(context), "evidence_ref": _evidence_ref("check"),
                    "exit_code": None, "process_tree_state": "unknown",
                    "requires_reconciliation": True, "evidence_scope": "binding_lost"}
        if result is None:
            status, error_code, exit_code = "unknown", "runner_error", None
            tree_state, reconciliation, evidence_scope = "unknown", True, "unavailable"
        else:
            status = result.outcome if result.outcome in {"passed", "failed", "unknown"} else "unknown"
            if result.error_code == "command_not_allowed":
                status = "unknown"
            error_code = _safe_error_code(result.error_code)
            exit_code = result.exit_code if type(result.exit_code) is int else None
            tree_state = result.process_tree_state if result.process_tree_state in {"not_started", "stopped", "unknown"} else "unknown"
            reconciliation = result.requires_reconciliation if type(result.requires_reconciliation) is bool else True
            evidence_scope = result.evidence_scope if type(result.evidence_scope) is str else "unknown"
        digest = _current_digest(context)
        if not isinstance(digest, str) or not digest or len(digest) > 256:
            status, digest, tree_state, reconciliation = "unknown", str(getattr(context, "candidate_digest", "unknown")), "unknown", True
        return {
            "status": status, "check_id": check_id, "candidate_digest": digest,
            "evidence_ref": _evidence_ref("check"), "exit_code": exit_code,
            "process_tree_state": tree_state, "requires_reconciliation": reconciliation,
            "evidence_scope": evidence_scope, "error_code": error_code,
        }

    tools = [
        HostTool("observe_candidate", "Inspect a bounded inventory of the host-bound candidate.", observe),
        HostTool("read_candidate_file", "Read a candidate file (max 4 MiB), redacted and returned in 8 KiB UTF-8-safe segments. Continue with @read:<next_offset>:<sha256>:<relative-path>. To locate text in one call, use @find:<base64url-utf8-query>:<relative-path> (query <=512 UTF-8 bytes); returns up to 5 redacted contexts, exact count and raw file SHA-256.", read_file),
        HostTool("write_candidate_file", "Write a relative candidate file atomically (max 4 MiB); ordered JSON chunks in content use {$candidate_write_chunk:1,write_id,index,total_bytes,sha256,data}, base64 data <=9 KiB, with <=4 MiB aggregate buffered. For one-call exact edits set content to JSON with $candidate_edit=1, base_sha256=<64 hex>, old=<non-empty>, new=<non-empty>; old must occur exactly once.", write_file),
    ]
    if include_verification:
        tools.append(HostTool("run_registered_check", "Run one host-registered verification check by ID.", run_check))
    return tuple(tools)


def _current_digest(context: SelfEvolutionWorkerContext) -> str | None:
    try:
        value = context.current_candidate_digest() if context.current_candidate_digest else None
    except Exception:
        return None
    return value if type(value) is str and value and value == value.strip() and len(value) <= 256 else None


def _is_link_or_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # Windows may update ctime when opening/reading; mtime plus object identity
    # detects content replacement without treating access metadata as a mutation.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, getattr(info, "st_mtime_ns", 0))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _evidence_ref(kind: str) -> str:
    return f"self-evolution://{kind}/{secrets.token_hex(16)}"


def _safe_error_code(value: object) -> str:
    if type(value) is not str or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value):
        return "verification_unknown"
    return value
