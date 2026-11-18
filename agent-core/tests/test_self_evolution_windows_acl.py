from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from self_evolution_windows_acl import WindowsAclError, audit_private_path


pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows ACLs")


def test_native_acl_audit_returns_identity_from_the_audited_handle(tmp_path):
    target = tmp_path / "private.sqlite3"
    target.write_bytes(b"not a SQLite database; ACL audit only")

    identity = audit_private_path(target, expected_type="file")

    assert isinstance(identity, tuple)
    assert len(identity) == 3
    assert all(type(part) is int for part in identity)
    stat = target.stat()
    assert identity[1:] == (stat.st_ino >> 32, stat.st_ino & 0xFFFFFFFF)
    assert identity[0] > 0


def test_native_acl_audit_rejects_everyone_write_access_even_if_path_exists(tmp_path):
    import shutil
    import subprocess

    icacls = shutil.which("icacls.exe") or shutil.which("icacls")
    if not icacls:
        pytest.fail("Windows icacls.exe is required for the real ACL-denial test")
    target = tmp_path / "world-writable.sqlite3"
    target.write_bytes(b"ACL denial probe")
    changed = subprocess.run(
        [icacls, str(target), "/grant", "*S-1-1-0:(M)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0, changed.stdout + changed.stderr

    with pytest.raises(WindowsAclError):
        audit_private_path(target, expected_type="file")



@pytest.mark.parametrize("actual,expected", [("file", "directory"), ("directory", "file")])
def test_native_audit_rejects_wrong_handle_type(tmp_path, actual, expected):
    target = tmp_path / "target"
    if actual == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"data")
    with pytest.raises(WindowsAclError, match="type"):
        audit_private_path(target, expected_type=expected)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_native_audit_accepts_matching_handle_type(tmp_path, kind):
    target = tmp_path / "target"
    if kind == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"data")
    assert audit_private_path(target, expected_type=kind)


def test_native_audit_can_open_metadata_with_exclusive_read_handle(tmp_path):
    import ctypes
    target = tmp_path / "locked-shm"
    target.write_bytes(b"data")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                   ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(target), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        assert audit_private_path(target, expected_type="file")
    finally:
        kernel.CloseHandle(handle)


def test_private_storage_verifier_returns_reusable_proofs_and_rejects_paths_outside_root(tmp_path):
    from self_evolution_windows_acl import create_private_storage_acl_verifier

    root = tmp_path / "private-state"
    root.mkdir()
    database = root / "state.sqlite3"
    database.write_bytes(b"acl probe")
    verifier = create_private_storage_acl_verifier(root)

    proof = verifier.audit(database)

    assert proof.root_identity == verifier.root_identity
    assert proof.target_path == database.resolve()
    assert proof.expected_type == "file"
    assert len(proof.target_identity) == 3
    assert verifier(database) is True
    with pytest.raises(WindowsAclError, match="outside"):
        verifier(tmp_path / "foreign.sqlite3")


def test_private_storage_verifier_rejects_reparse_components(tmp_path):
    from self_evolution_windows_acl import create_private_storage_acl_verifier

    root = tmp_path / "private-state"
    root.mkdir()
    verifier = create_private_storage_acl_verifier(root)
    target = root / "linked.sqlite3"
    foreign = tmp_path / "foreign.sqlite3"
    foreign.write_bytes(b"foreign")
    try:
        target.symlink_to(foreign)
    except OSError as exc:
        pytest.skip(f"symlink privilege unavailable: {exc}")

    with pytest.raises(WindowsAclError):
        verifier.audit(target)
