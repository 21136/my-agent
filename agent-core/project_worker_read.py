"""Single-project text reads using Windows directory-relative handles.

Only trusted host code may bind the root. This is not a worker or OS sandbox;
privileged kernel/filesystem manipulation is outside this boundary. Reads fail
closed if identities, paths, types or limits cannot be checked. Other platforms
are unsupported. The root is fixed by file identity at construction time.
"""
from __future__ import annotations
import ctypes
import os
import re
from contextlib import ExitStack
from pathlib import Path

MAX_FILES = 32
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
_RESERVED = re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\..*)?$', re.IGNORECASE)

class ProjectReadError(Exception):
    """Request cannot be served safely; no content returned."""

class ProjectReadTransientError(ProjectReadError):
    """A specifically whitelisted Windows sharing/lock failure during I/O."""
    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(error_code)

_RETRYABLE_NTSTATUS = {
    0xC0000043: "sharing_violation",       # STATUS_SHARING_VIOLATION
    0xC0000054: "file_lock_conflict",      # STATUS_FILE_LOCK_CONFLICT
    0xC0000055: "lock_not_granted",        # STATUS_LOCK_NOT_GRANTED
}
_RETRYABLE_WINERROR = {
    32: "sharing_violation",               # ERROR_SHARING_VIOLATION
    33: "lock_violation",                  # ERROR_LOCK_VIOLATION
}

def _valid_relative_path(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 1024:
        return False
    if any(ord(c) < 32 or ord(c) == 127 or c in '\\:<>"|?*~' for c in value):
        return False
    for component in value.split('/'):
        if (not component or component in ('.', '..') or component.lower() == 'host'
                or component[-1] in ' .' or _RESERVED.fullmatch(component) is not None):
            return False
    return True

if os.name == 'nt':
    from ctypes import wintypes as w
    class _UnicodeString(ctypes.Structure):
        _fields_ = [('Length',w.USHORT),('MaximumLength',w.USHORT),('Buffer',w.LPWSTR)]
    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [('Length',w.ULONG),('RootDirectory',w.HANDLE),('ObjectName',ctypes.POINTER(_UnicodeString)),
                    ('Attributes',w.ULONG),('SecurityDescriptor',w.LPVOID),('SecurityQualityOfService',w.LPVOID)]
    class _IOStatus(ctypes.Structure):
        _fields_ = [('Status',ctypes.c_void_p),('Information',ctypes.c_size_t)]
    class _Info(ctypes.Structure):
        _fields_ = [('attributes',w.DWORD),('created',w.FILETIME),('accessed',w.FILETIME),('written',w.FILETIME),
                    ('volume',w.DWORD),('size_high',w.DWORD),('size_low',w.DWORD),('links',w.DWORD),
                    ('index_high',w.DWORD),('index_low',w.DWORD)]
    _k = ctypes.WinDLL('kernel32',use_last_error=True)
    _n = ctypes.WinDLL('ntdll',use_last_error=True)
    _k.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    _k.CreateFileW.restype = w.HANDLE
    _k.CloseHandle.argtypes = [w.HANDLE]
    _k.GetFileInformationByHandle.argtypes = [w.HANDLE,ctypes.POINTER(_Info)]
    _k.GetFileInformationByHandle.restype = w.BOOL
    _k.GetFileType.argtypes = [w.HANDLE]
    _k.GetFileType.restype = w.DWORD
    _k.GetDriveTypeW.argtypes = [w.LPCWSTR]
    _k.GetDriveTypeW.restype = w.UINT
    _k.ReadFile.argtypes = [w.HANDLE,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD),w.LPVOID]
    _k.ReadFile.restype = w.BOOL
    _n.NtCreateFile.argtypes = [ctypes.POINTER(w.HANDLE),w.ULONG,ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IOStatus),ctypes.c_void_p,w.ULONG,w.ULONG,w.ULONG,w.ULONG,ctypes.c_void_p,w.ULONG]
    _n.NtCreateFile.restype = ctypes.c_long

def _open_child(parent: int, name: str, *, directory: bool) -> int:
    # Exactly one component, opened from the previously verified directory handle.
    if not name or any(c in name for c in '\\/:') or name in ('.','..'):
        raise ProjectReadError('invalid component')
    encoded=name.encode('utf-16-le'); buf=ctypes.create_unicode_buffer(name)
    if len(encoded) > 65532: raise ProjectReadError('invalid component')
    text=_UnicodeString(len(encoded),len(encoded)+2,ctypes.cast(buf,w.LPWSTR))
    attrs=_ObjectAttributes(ctypes.sizeof(_ObjectAttributes),parent,ctypes.pointer(text),0x40,None,None)
    io=_IOStatus(); handle=w.HANDLE()
    access=0x00100080 if directory else 0x80100080
    options=0x00200000 | 0x20 | (0x1 if directory else 0x40)
    status=_n.NtCreateFile(ctypes.byref(handle),access,ctypes.byref(attrs),ctypes.byref(io),None,0,1,1,options,None,0)
    if status != 0 or not handle.value:
        retry_code = _RETRYABLE_NTSTATUS.get(status & 0xFFFFFFFF)
        if retry_code is not None:
            raise ProjectReadTransientError(retry_code)
        raise ProjectReadError('component unavailable')
    return handle.value

def _inspect(handle: int, *, directory: bool, allow_zero_file_id: bool = False) -> _Info:
    info=_Info()
    if (not _k.GetFileInformationByHandle(handle,ctypes.byref(info)) or _k.GetFileType(handle)!=1
            or info.attributes & 0x400 or bool(info.attributes & 0x10) != directory
            or not info.volume
            or (not (info.index_high or info.index_low) and not (allow_zero_file_id and directory))
            or (not directory and info.links != 1)):
        raise ProjectReadError('unsafe component')
    return info

def _identity(info: _Info) -> tuple[int,int,int]:
    return info.volume,info.index_high,info.index_low

def _child(stack: ExitStack, parent: int, name: str, *, directory: bool) -> tuple[int,_Info]:
    handle=_open_child(parent,name,directory=directory)
    stack.callback(_k.CloseHandle,handle)
    return handle,_inspect(handle,directory=directory)

class ProjectTextReader:
    """The project root must be supplied by a trusted host, not model input."""
    def __init__(self, project_root: Path):
        if not isinstance(project_root,Path) or not project_root.is_absolute():
            raise ProjectReadError('invalid trusted project binding')
        self._project_root=project_root
        self._root_identity=None
        if os.name == 'nt':
            try:
                with ExitStack() as stack:
                    _,info=self._root(stack)
                    self._root_identity=_identity(info)
            except Exception:
                raise ProjectReadError('project binding unavailable') from None

    def _root(self, stack: ExitStack) -> tuple[int,_Info]:
        path=self._project_root; anchor=path.anchor
        if (not re.fullmatch(r'[A-Za-z]:\\',anchor) or not path.parts[1:] or _k.GetDriveTypeW(anchor)!=3
                or any(not _valid_relative_path(part) for part in path.parts[1:])):
            raise ProjectReadError('invalid project binding')
        root=_k.CreateFileW(anchor,0x80,1,None,3,0x02200000,None)
        if root == ctypes.c_void_p(-1).value: raise ProjectReadError('root unavailable')
        stack.callback(_k.CloseHandle,root)
        info=_inspect(root,directory=True,allow_zero_file_id=True); volume=info.volume
        for component in path.parts[1:]:
            root,info=_child(stack,root,component,directory=True)
            if info.volume != volume: raise ProjectReadError('volume changed')
        return root,info

    def _walk(self, stack: ExitStack, root: int, parts: list[str], volume: int) -> tuple[int,list[tuple[int,int,int]],_Info]:
        identities=[]; parent=root
        for i,name in enumerate(parts):
            parent,info=_child(stack,parent,name,directory=i<len(parts)-1)
            if info.volume != volume: raise ProjectReadError('volume changed')
            identities.append(_identity(info))
        return parent,identities,info

    def read_texts(self, relative_paths: list[str]) -> dict[str,str]:
        if (type(relative_paths) is not list or not 1 <= len(relative_paths) <= MAX_FILES
                or any(not _valid_relative_path(path) for path in relative_paths)
                or len({path.casefold() for path in relative_paths}) != len(relative_paths)):
            raise ProjectReadError('invalid project read request')
        if os.name != 'nt': raise ProjectReadError('secure project text reading unavailable')
        try:
            with ExitStack() as stack:
                root,root_info=self._root(stack)
                if _identity(root_info)!=self._root_identity: raise ProjectReadError('binding changed')
                result={}; observations=[]; total=0
                for relative in relative_paths:
                    parts=relative.split('/')
                    handle,ids,info=self._walk(stack,root,parts,root_info.volume)
                    size=(info.size_high<<32)|info.size_low; total+=size
                    if size>MAX_FILE_BYTES or total>MAX_TOTAL_BYTES: raise ProjectReadError('file limit')
                    buffer=ctypes.create_string_buffer(size+1); count=w.DWORD()
                    if not _k.ReadFile(handle,buffer,size+1,ctypes.byref(count),None):
                        retry_code = _RETRYABLE_WINERROR.get(ctypes.get_last_error())
                        if retry_code is not None:
                            raise ProjectReadTransientError(retry_code)
                        raise ProjectReadError('read incomplete')
                    if count.value != size:
                        raise ProjectReadError('read incomplete')
                    raw=buffer.raw[:size]
                    if b'\x00' in raw: raise ProjectReadError('not text')
                    result[relative]=raw.decode('utf-8',errors='strict')
                    observations.append((parts,ids,size))
                # Reopen through the bound path before returning; catch replacement
                # while retaining all original handles and without returning partial data.
                check_root,check_info=self._root(stack)
                if _identity(check_info)!=self._root_identity: raise ProjectReadError('binding changed')
                for parts,ids,size in observations:
                    _,current,info=self._walk(stack,check_root,parts,root_info.volume)
                    if current!=ids or (info.size_high<<32)|info.size_low != size:
                        raise ProjectReadError('path changed')
                return result
        except ProjectReadTransientError:
            raise
        except Exception:
            raise ProjectReadError('project read unavailable or unsafe') from None
