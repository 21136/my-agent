"""Persistent per-user DPAPI storage for the Self-Evolution Host integrity key.

The key is never written in plaintext, printed, returned in diagnostics, or
silently regenerated after corruption. This module intentionally supports only
Windows current-user DPAPI; it has no weaker cross-platform fallback.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Any

KEY_BYTES = 32
_MAX_ENVELOPE_BYTES = 4096
_DPAPI_UI_FORBIDDEN = 0x1
_ENTROPY = b"my-agent/self-evolution/host-integrity-key/v1"
_DESCRIPTION = "My Agent Self-Evolution Host integrity key"


class HostIntegrityKeyError(RuntimeError):
    """Base class for sanitized Host key persistence failures."""

    error_code = "host_integrity_key_unavailable"


class HostIntegrityKeyUnavailable(HostIntegrityKeyError):
    """No supported private persistence/crypto provider is available."""


class HostIntegrityKeyCorrupt(HostIntegrityKeyError):
    """An existing key envelope is malformed or cannot be authenticated."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes):
    if not data:
        raise ValueError("empty DPAPI input")
    backing = ctypes.create_string_buffer(data, len(data))
    value = _DataBlob(len(data), ctypes.cast(backing, ctypes.POINTER(ctypes.c_ubyte)))
    return value, backing


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise HostIntegrityKeyUnavailable("Windows current-user DPAPI is required")
    try:
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
        crypt.CryptProtectData.restype = wintypes.BOOL
        crypt.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
        crypt.CryptUnprotectData.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        incoming, incoming_buffer = _blob(data)
        entropy, entropy_buffer = _blob(_ENTROPY)
        outgoing = _DataBlob()
        description = wintypes.LPWSTR()
        if protect:
            ok = crypt.CryptProtectData(
                ctypes.byref(incoming), _DESCRIPTION, ctypes.byref(entropy), None,
                None, _DPAPI_UI_FORBIDDEN, ctypes.byref(outgoing),
            )
        else:
            ok = crypt.CryptUnprotectData(
                ctypes.byref(incoming), ctypes.byref(description), ctypes.byref(entropy),
                None, None, _DPAPI_UI_FORBIDDEN, ctypes.byref(outgoing),
            )
        # Keep the backing buffers live through the native call.
        _ = incoming_buffer, entropy_buffer
        if not ok:
            raise HostIntegrityKeyCorrupt("DPAPI rejected the existing Host key envelope")
        if not outgoing.pbData or not 1 <= outgoing.cbData <= _MAX_ENVELOPE_BYTES:
            raise HostIntegrityKeyCorrupt("DPAPI returned an invalid Host key payload")
        result = ctypes.string_at(outgoing.pbData, outgoing.cbData)
        return result
    except (HostIntegrityKeyError,):
        raise
    except Exception as exc:
        if protect:
            raise HostIntegrityKeyUnavailable("Windows DPAPI protection failed") from exc
        raise HostIntegrityKeyCorrupt("Windows DPAPI could not authenticate the Host key") from exc
    finally:
        if "outgoing" in locals() and outgoing.pbData:
            kernel.LocalFree(ctypes.cast(outgoing.pbData, ctypes.c_void_p))
        if "description" in locals() and description:
            kernel.LocalFree(ctypes.cast(description, ctypes.c_void_p))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate envelope member")
        result[key] = value
    return result


def _decode_envelope(raw: bytes) -> bytes:
    if not raw or len(raw) > _MAX_ENVELOPE_BYTES:
        raise HostIntegrityKeyCorrupt("Host key envelope size is invalid")
    try:
        payload = json.loads(raw.decode("ascii"), object_pairs_hook=_reject_duplicate_keys)
        if (type(payload) is not dict or set(payload) != {"format", "protection", "blob"}
                or type(payload.get("format")) is not int or payload["format"] != 1
                or payload.get("protection") != "windows_dpapi_current_user"
                or type(payload.get("blob")) is not str or not payload["blob"]):
            raise ValueError("unsupported key envelope")
        protected = base64.b64decode(payload["blob"], validate=True)
        if not protected or len(protected) > _MAX_ENVELOPE_BYTES:
            raise ValueError("invalid protected blob size")
        key = _dpapi(protected, protect=False)
        if len(key) != KEY_BYTES:
            raise ValueError("invalid unprotected key size")
        return key
    except HostIntegrityKeyError:
        raise
    except Exception as exc:
        raise HostIntegrityKeyCorrupt("Host key envelope is malformed") from exc


def _encode_envelope(key: bytes) -> bytes:
    protected = _dpapi(key, protect=True)
    payload = {
        "format": 1,
        "protection": "windows_dpapi_current_user",
        "blob": base64.b64encode(protected).decode("ascii"),
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def _ensure_private_parent(path: Path):
    from self_evolution_windows_acl import create_private_storage_acl_verifier

    parent = path.parent
    try:
        # Do not create through a pre-existing linked/reparse component. After
        # mkdir, native owner+DACL evidence is required before any key is read/written.
        for component in (parent, *parent.parents):
            if component.exists() or component == component.anchor:
                info = component.lstat()
                if component.is_symlink() or bool(
                    getattr(info, "st_file_attributes", 0) & 0x400
                ):
                    raise HostIntegrityKeyUnavailable("linked key storage path is not accepted")
        parent.mkdir(parents=True, exist_ok=True)
        verifier = create_private_storage_acl_verifier(parent.resolve(strict=True))
        verifier.audit(parent.resolve(strict=True))
        return verifier
    except HostIntegrityKeyError:
        raise
    except Exception as exc:
        raise HostIntegrityKeyUnavailable("private Host key directory could not be verified") from exc


def _read_existing(path: Path, verifier) -> bytes:
    from self_evolution_windows_acl import WindowsAclError

    try:
        before = verifier.audit(path)
        with path.open("rb") as stream:
            raw = stream.read(_MAX_ENVELOPE_BYTES + 1)
        after = verifier.audit(path)
        if before.target_identity != after.target_identity or before.target_path != after.target_path:
            raise HostIntegrityKeyCorrupt("Host key file identity changed during read")
        return _decode_envelope(raw)
    except HostIntegrityKeyError:
        raise
    except (OSError, ValueError, WindowsAclError) as exc:
        raise HostIntegrityKeyCorrupt("existing Host key could not be safely read") from exc


def load_or_create_host_integrity_key(
    path: str | os.PathLike[str], *, acl_verifier=None,
) -> bytes:
    """Read or atomically create one stable 32-byte current-user DPAPI key.

    Existing corrupt files are never replaced. Concurrent creators race through
    a same-directory temporary file and Windows no-replace rename; the loser
    discards only its own temporary and reads the winner's authenticated key.
    """
    if os.name != "nt":
        raise HostIntegrityKeyUnavailable("Windows current-user DPAPI is required")
    try:
        target = Path(path)
        if not target.is_absolute() or target.name in {"", ".", ".."}:
            raise ValueError("absolute key file path required")
        target = Path(os.path.abspath(target))
    except (TypeError, ValueError, OSError) as exc:
        raise HostIntegrityKeyUnavailable("invalid Host key path") from exc

    verifier = acl_verifier if acl_verifier is not None else _ensure_private_parent(target)
    from self_evolution_windows_acl import PrivateStorageAclVerifier
    if type(verifier) is not PrivateStorageAclVerifier:
        raise HostIntegrityKeyUnavailable("native private-storage verifier required")
    if verifier.root != target.parent.resolve(strict=True):
        raise HostIntegrityKeyUnavailable("Host key path is outside its audited private root")
    verifier.audit(verifier.root)
    if target.exists():
        return _read_existing(target, verifier)

    key = secrets.token_bytes(KEY_BYTES)
    envelope = _encode_envelope(key)
    temporary: Path | None = None
    try:
        fd, raw_temp = tempfile.mkstemp(prefix=".host-key-", suffix=".tmp", dir=target.parent)
        temporary = Path(raw_temp)
        with os.fdopen(fd, "wb") as stream:
            stream.write(envelope)
            stream.flush()
            os.fsync(stream.fileno())
        verifier.audit(temporary)
        try:
            # On Windows os.rename is same-volume and fails rather than replacing
            # an existing destination. This is the publication race boundary.
            os.rename(temporary, target)
            temporary = None
        except OSError:
            if target.exists():
                return _read_existing(target, verifier)
            raise
        verifier.audit(target)
        return key
    except HostIntegrityKeyError:
        raise
    except Exception as exc:
        raise HostIntegrityKeyUnavailable("Host key could not be atomically persisted") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
