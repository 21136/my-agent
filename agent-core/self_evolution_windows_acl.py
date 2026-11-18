from __future__ import annotations

import ctypes
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final


class WindowsAclError(RuntimeError):
    """A Windows path could not be proven private from its opened handle."""

    def __init__(self, message: str, *, winerror: int | None = None) -> None:
        super().__init__(message)
        self.winerror = winerror


# (volume serial, file-index high, file-index low), read from the same handle
# used to retrieve the owner and DACL. This is an audit result, not a SQLite
# handle binding or a defense against all path replacement races.
FileIdentity = tuple[int, int, int]


_FILE_READ_ATTRIBUTES: Final = 0x0080
_READ_CONTROL: Final = 0x00020000
_FILE_SHARE_READ: Final = 0x00000001
_FILE_SHARE_WRITE: Final = 0x00000002
_OPEN_EXISTING: Final = 3
_FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x00000400
_FILE_ATTRIBUTE_DIRECTORY: Final = 0x00000010
_OWNER_SECURITY_INFORMATION: Final = 0x00000001
_DACL_SECURITY_INFORMATION: Final = 0x00000004
_SE_FILE_OBJECT: Final = 1
_ACL_SIZE_INFORMATION: Final = 2
_ACCESS_ALLOWED_ACE_TYPE: Final = 0
_ACCESS_DENIED_ACE_TYPE: Final = 1
_DANGEROUS_ACCESS: Final = 0x80000000 | 0x40000000 | 0x20000000 | 0x10000000 | 0x00000001 | 0x00000002 | 0x00000004 | 0x00000010 | 0x00000020 | 0x00000040 | 0x00000080 | 0x00010000 | 0x00040000 | 0x00080000
_TOKEN_QUERY: Final = 0x0008
_TOKEN_USER: Final = 1
_TRUSTED_SIDS: Final = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-4"})  # SYSTEM, Administrators, verified owner rights
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime", _FileTime),
        ("ftLastAccessTime", _FileTime),
        ("ftLastWriteTime", _FileTime),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", ctypes.c_uint32),
        ("AclBytesInUse", ctypes.c_uint32),
        ("AclBytesFree", ctypes.c_uint32),
    ]


def _win_error(message: str) -> WindowsAclError:
    error = ctypes.get_last_error()
    return WindowsAclError(f"{message} (Win32 error {error}): {ctypes.FormatError(error).strip()}", winerror=error)


def _sid_string(advapi32, kernel32, sid) -> str:
    text = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise _win_error("cannot decode Windows security identifier")
    try:
        return text.value
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _current_user_sid(advapi32, kernel32) -> str:
    advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _win_error("cannot open current process token")
    try:
        advapi32.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                                 ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        advapi32.GetTokenInformation.restype = ctypes.c_int
        needed = ctypes.c_uint32()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise _win_error("cannot size current process token information")
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, needed, ctypes.byref(needed)):
            raise _win_error("cannot read current process token information")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        return _sid_string(advapi32, kernel32, sid)
    finally:
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(token)


def _audit_dacl(advapi32, kernel32, owner, dacl) -> None:
    """Conservative ACE inspection, not a full effective access calculation.

    It may reject grants neutralized by denies or inheritance flags; uncertainty
    is intentionally rejected rather than evaluated as effective permission.
    """
    if not owner or not dacl:
        raise WindowsAclError("missing owner or DACL (null DACL is not private)")
    current_user = _current_user_sid(advapi32, kernel32)
    if _sid_string(advapi32, kernel32, owner) != current_user:
        raise WindowsAclError("storage owner is not the current user")

    info = _AclSizeInformation()
    if not advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), _ACL_SIZE_INFORMATION):
        raise _win_error("cannot inspect Windows DACL")
    trusted = _TRUSTED_SIDS | {current_user}
    for index in range(info.AceCount):
        ace = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
            raise _win_error("cannot read Windows DACL entry")
        address = ace.value
        ace_type = ctypes.c_ubyte.from_address(address).value
        if ace_type not in (_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE):
            raise WindowsAclError("unsupported DACL entry type; refusing uncertain ACL")
        # ACCESS_*_ACE: ACE_HEADER (4 bytes), access mask (4), then SID.
        sid = ctypes.c_void_p(address + 8)
        if not advapi32.IsValidSid(sid):
            raise WindowsAclError("malformed SID in Windows DACL")
        principal = _sid_string(advapi32, kernel32, sid)
        mask = ctypes.c_uint32.from_address(address + 4).value
        if (ace_type == _ACCESS_ALLOWED_ACE_TYPE and principal not in trusted
                and mask & _DANGEROUS_ACCESS):
            raise WindowsAclError(f"DACL grants data access to an untrusted principal: {principal}")


def audit_private_path(path: str | os.PathLike[str], *, expected_type: str) -> FileIdentity:
    """Audit owner and DACL through a handle, returning that handle's identity.

    The open omits FILE_SHARE_DELETE to reduce replacement while the audit
    handle exists. The returned identity can be compared by a caller, but this
    does not bind Python sqlite3's private handle to this handle and does not
    claim to eliminate path TOCTOU, WAL recovery, or directory races.
    """
    if expected_type not in {"file", "directory"}:
        raise WindowsAclError("invalid expected file type")
    if os.name != "nt":
        raise WindowsAclError("native ACL audit is only available on Windows")
    candidate = Path(path)
    try:
        if not candidate.is_absolute():
            raise WindowsAclError("ACL audit requires an absolute path")
        value = str(candidate)
    except (TypeError, ValueError, OSError) as exc:
        raise WindowsAclError("invalid ACL audit path") from exc

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation)]
    kernel32.GetFileInformationByHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.GetSecurityInfo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32,
                                         ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetSecurityInfo.restype = ctypes.c_uint32
    advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
    advapi32.GetAclInformation.restype = ctypes.c_int
    advapi32.GetAce.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = ctypes.c_int
    advapi32.IsValidSid.argtypes = [ctypes.c_void_p]
    advapi32.IsValidSid.restype = ctypes.c_int
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_int
    handle = kernel32.CreateFileW(
        value, _READ_CONTROL | _FILE_READ_ATTRIBUTES, _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None, _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if handle == _INVALID_HANDLE_VALUE or handle is None:
        raise _win_error("cannot open path for ACL audit")

    security_descriptor = ctypes.c_void_p()
    try:
        info = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise _win_error("cannot identify ACL-audited handle")
        if info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise WindowsAclError("reparse points are not accepted for ACL audit")
        is_directory = bool(info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY)
        if is_directory != (expected_type == "directory"):
            raise WindowsAclError(f"audited handle type does not match expected {expected_type}")
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        status = advapi32.GetSecurityInfo(
            handle, _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(security_descriptor),
        )
        if status:
            raise WindowsAclError(f"cannot read security descriptor from handle (Win32 error {status})")
        _audit_dacl(advapi32, kernel32, owner, dacl)
        return (int(info.dwVolumeSerialNumber), int(info.nFileIndexHigh), int(info.nFileIndexLow))
    finally:
        if security_descriptor:
            kernel32.LocalFree(security_descriptor)
        kernel32.CloseHandle(handle)



@dataclass(frozen=True, slots=True)
class PrivateStorageAclProof:
    """Point-in-time native ACL proof for a path beneath one stable private root.

    This proof describes the audited handles and identities only. It is not bound
    to SQLite's own handles and does not prove WAL/recovery lifetime safety.
    """

    root_path: Path
    root_identity: FileIdentity
    target_path: Path
    target_identity: FileIdentity
    expected_type: str
    audited_at_ns: int


class PrivateStorageAclVerifier:
    """Callable, root-bounded ACL verifier reusable by EvolutionStore.

    Creation requires an existing native-audited directory. Each call rechecks
    the root identity, rejects linked/reparse components, and audits the target
    itself through a Windows handle.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if os.name != "nt":
            raise WindowsAclError("native ACL verifier is only available on Windows")
        supplied = Path(root)
        if not supplied.is_absolute():
            raise WindowsAclError("private storage root must be absolute")
        self.root = supplied
        self._reject_reparse_chain(self.root)
        try:
            self.root = self.root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WindowsAclError("private storage root is unavailable") from exc
        self.root_identity = audit_private_path(self.root, expected_type="directory")

    @staticmethod
    def _reject_reparse_chain(path: Path) -> None:
        chain = (path, *path.parents)
        for component in chain:
            try:
                info = component.lstat()
            except OSError as exc:
                raise WindowsAclError("private storage path component is unavailable") from exc
            if component.is_symlink() or bool(
                getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise WindowsAclError("reparse points are not accepted in private storage paths")

    def audit(self, path: str | os.PathLike[str]) -> PrivateStorageAclProof:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise WindowsAclError("ACL target must be absolute")
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise WindowsAclError("ACL target is outside the trusted private root") from exc
        self._reject_reparse_chain(candidate)
        current = self.root
        for part in relative.parts:
            current = current / part
            try:
                info = current.lstat()
            except OSError as exc:
                raise WindowsAclError("ACL target component is unavailable") from exc
            is_final = current == candidate
            if not is_final and not stat.S_ISDIR(info.st_mode):
                raise WindowsAclError("non-directory component in private storage path")
        try:
            root_id = audit_private_path(self.root, expected_type="directory")
            if root_id != self.root_identity:
                raise WindowsAclError("trusted private root identity changed")
            resolved = candidate.resolve(strict=True)
            if resolved != candidate:
                raise WindowsAclError("ACL target canonical identity changed")
            final_info = candidate.lstat()
            expected_type = "directory" if stat.S_ISDIR(final_info.st_mode) else "file"
            target_id = audit_private_path(candidate, expected_type=expected_type)
            # Recheck root after target audit to bound root replacement around the
            # two handle reads. This narrows, but cannot eliminate, path races.
            if audit_private_path(self.root, expected_type="directory") != self.root_identity:
                raise WindowsAclError("trusted private root identity changed during audit")
        except WindowsAclError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise WindowsAclError("private storage ACL audit failed") from exc
        return PrivateStorageAclProof(
            root_path=self.root, root_identity=self.root_identity,
            target_path=candidate, target_identity=target_id,
            expected_type=expected_type, audited_at_ns=time.time_ns(),
        )

    def __call__(self, path: str | os.PathLike[str]) -> bool:
        self.audit(path)
        return True


def create_private_storage_acl_verifier(
    root: str | os.PathLike[str],
) -> PrivateStorageAclVerifier:
    """Create a reusable verifier from an existing Windows-private directory."""
    return PrivateStorageAclVerifier(root)
