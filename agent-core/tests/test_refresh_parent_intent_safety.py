from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_linked_project_session_delivery import setup_copy
import multi_project_work_record_bindings as bindings


@pytest.mark.skipif(sys.platform != 'win32',reason='Windows owned-handle publication')
def test_parent_intent_publisher_must_not_delete_replaced_temp(tmp_path,monkeypatch):
    import os
    parent, ns, rows, *_ = setup_copy(tmp_path)
    before=ns._path.read_bytes()
    payload=ns._read(); payload['revision']+=1
    replacement=[]; moved=[]
    def race(handle, temporary_path, target):
        temporary_path=Path(temporary_path)
        moved_path=temporary_path.with_suffix('.held-by-other')
        os.rename(temporary_path,moved_path)
        temporary_path.write_bytes(b'OTHER PROCESS FILE')
        replacement.append(temporary_path);moved.append(moved_path)
        raise OSError('simulated concurrent replacement')
    monkeypatch.setattr(bindings,'_replace_parent_from_handle',race,raising=False)
    with pytest.raises(bindings.WorkRecordWriteError):
        ns._publish(payload)
    assert ns._path.read_bytes()==before
    assert replacement and replacement[0].read_bytes()==b'OTHER PROCESS FILE'
    assert moved[0].exists(), 'uncertain rename retains the owned file'


@pytest.mark.skipif(sys.platform != 'win32',reason='Windows owned-handle publication')
def test_parent_intent_publisher_renames_owned_handle_not_replacement(tmp_path,monkeypatch):
    import os
    parent, ns, rows, *_ = setup_copy(tmp_path)
    payload=ns._read();payload['revision']+=1
    replacement=[];moved=[]
    original=bindings._replace_parent_from_handle
    def race(handle,temporary_path,target):
        temporary_path=Path(temporary_path); moved_path=temporary_path.with_suffix('.moved')
        os.rename(temporary_path,moved_path)
        temporary_path.write_bytes(b'OTHER PROCESS FILE')
        replacement.append(temporary_path);moved.append(moved_path)
        return original(handle,temporary_path,target)
    monkeypatch.setattr(bindings,'_replace_parent_from_handle',race)
    ns._publish(payload)
    assert ns._read()==payload
    assert replacement[0].read_bytes()==b'OTHER PROCESS FILE'
    assert not moved[0].exists()


@pytest.mark.skipif(sys.platform != 'win32',reason='Windows handle probe')
def test_windows_handle_disposition_preserves_replacement(tmp_path):
    import ctypes
    import msvcrt
    import os
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    setinfo=kernel.SetFileInformationByHandle
    setinfo.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    setinfo.restype=wintypes.BOOL
    name=tmp_path/'owned.tmp';moved=tmp_path/'owned-moved.tmp'
    # DELETE + GENERIC_WRITE, share delete/read/write, CREATE_NEW, OPEN_REPARSE_POINT.
    handle=create(str(name),0x40000000|0x00010000,7,None,1,0x80|0x00200000,None)
    assert handle!=wintypes.HANDLE(-1).value,ctypes.get_last_error()
    fd=msvcrt.open_osfhandle(handle,os.O_WRONLY|os.O_BINARY)
    try:
        os.write(fd,b'ORIGINAL');os.fsync(fd)
        os.rename(name,moved)
        name.write_bytes(b'REPLACEMENT')
        class Disposition(ctypes.Structure):
            _fields_=[('DeleteFile',wintypes.BOOL)]
        disposition=Disposition(1)
        assert setinfo(msvcrt.get_osfhandle(fd),4,ctypes.byref(disposition),ctypes.sizeof(disposition)),ctypes.get_last_error()
    finally:
        os.close(fd)
    assert name.read_bytes()==b'REPLACEMENT'
    assert not moved.exists()


@pytest.mark.skipif(sys.platform != 'win32',reason='Windows handle probe')
def test_windows_handle_rename_rejects_existing_target(tmp_path):
    import os
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    setinfo=kernel.SetFileInformationByHandle
    setinfo.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    setinfo.restype=wintypes.BOOL
    close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
    src=tmp_path/'source';dst=tmp_path/'target'
    dst.write_bytes(b'OTHER')
    handle=create(str(src),0x40000000|0x00010000,7,None,1,0x80|0x00200000,None)
    assert handle!=wintypes.HANDLE(-1).value,ctypes.get_last_error()
    target=str(dst)
    class Rename(ctypes.Structure):
        _fields_=[('ReplaceIfExists',wintypes.BOOL),('RootDirectory',wintypes.HANDLE),
                  ('FileNameLength',wintypes.DWORD),('FileName',wintypes.WCHAR*(len(target)+1))]
    rename=Rename(False,None,len(target.encode('utf-16-le')),target)
    try:
        result=setinfo(handle,3,ctypes.byref(rename),ctypes.sizeof(rename))
        error=ctypes.get_last_error()
        assert not result and error in {80,183},error
        write=kernel.WriteFile
        write.argtypes=[wintypes.HANDLE,wintypes.LPCVOID,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID]
        write.restype=wintypes.BOOL
        written=wintypes.DWORD()
        assert write(handle,b'OWNED',5,ctypes.byref(written),None) and written.value==5
        moved=tmp_path/'moved'; os.rename(src,moved)
        src.write_bytes(b'REPLACEMENT')
        destination=str(tmp_path/'published')
        class RenameNext(ctypes.Structure):
            _fields_=[('ReplaceIfExists',wintypes.BOOL),('RootDirectory',wintypes.HANDLE),
                      ('FileNameLength',wintypes.DWORD),('FileName',wintypes.WCHAR*(len(destination)+1))]
        next_rename=RenameNext(False,None,len(destination.encode('utf-16-le')),destination)
        assert setinfo(handle,3,ctypes.byref(next_rename),ctypes.sizeof(next_rename)),ctypes.get_last_error()
    finally:
        close(handle)
    assert src.read_bytes()==b'REPLACEMENT' and dst.read_bytes()==b'OTHER'
    assert (tmp_path/'published').read_bytes()==b'OWNED' and not moved.exists()


@pytest.mark.skipif(sys.platform != 'win32',reason='Windows owned-handle publication')
def test_parent_precommit_failure_deletes_only_owned_handle(tmp_path,monkeypatch):
    import os
    parent, ns, rows, *_ = setup_copy(tmp_path)
    before=ns._path.read_bytes();payload=ns._read();payload['revision']+=1
    original=ns._validate_scope; calls=[0]; replacement=[]; moved=[]
    def scope():
        calls[0]+=1
        if calls[0]==3:
            temp=next(parent.session_dir.glob('.multi-project-bindings.*.tmp'))
            owned=temp.with_suffix('.moved')
            os.rename(temp,owned)
            temp.write_bytes(b'OTHER PROCESS FILE')
            replacement.append(temp);moved.append(owned)
            raise bindings.WorkRecordScopeError('synthetic scope change')
        return original()
    monkeypatch.setattr(ns,'_validate_scope',scope)
    with pytest.raises(bindings.WorkRecordScopeError): ns._publish(payload)
    assert ns._path.read_bytes()==before
    assert replacement[0].read_bytes()==b'OTHER PROCESS FILE'
    assert not moved[0].exists()
