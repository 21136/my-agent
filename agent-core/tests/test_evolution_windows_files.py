from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
_SANDBOX = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from evolution.windows_files import WorkspaceFileError, exclusive_file


def _run_root() -> Path:
    value=os.environ.get("EVOLUTION_TEST_ROOT")
    assert value, "approved EVOLUTION_TEST_ROOT is required"
    root=Path(value).resolve(strict=True)
    assert root.is_relative_to((_SANDBOX/"artifacts").resolve(strict=True))
    assert (root/".evolution-test-run").read_text(encoding="utf-8-sig").strip()=="evolution-a"
    return root


def test_exclusive_handle_reads_writes_truncates_and_flushes_same_file(tmp_path: Path) -> None:
    if os.name != "nt": pytest.skip("Win32 CreateFileW contract is Windows-specific")
    assert tmp_path.resolve().is_relative_to(_run_root())
    file=tmp_path/"source.cjs"
    file.write_bytes(b"before bytes longer")
    with exclusive_file(file,create=False) as handle:
        assert handle.read_bytes()==b"before bytes longer"
        handle.write_bytes(b"after")
        assert handle.read_bytes()==b"after"
        handle.flush()
    assert file.read_bytes()==b"after"


def test_create_new_never_replaces_an_existing_file(tmp_path: Path) -> None:
    if os.name != "nt": pytest.skip("Win32 CreateFileW contract is Windows-specific")
    assert tmp_path.resolve().is_relative_to(_run_root())
    file=tmp_path/"exists.cjs"
    file.write_bytes(b"original")
    with pytest.raises(WorkspaceFileError) as caught:
        with exclusive_file(file,create=True): pass
    assert caught.value.code=="scope_conflict"
    assert file.read_bytes()==b"original"


def test_second_process_cannot_open_a_file_held_by_exclusive_handle(tmp_path: Path) -> None:
    if os.name != "nt": pytest.skip("Win32 CreateFileW contract is Windows-specific")
    assert tmp_path.resolve().is_relative_to(_run_root())
    file=tmp_path/"locked.cjs"
    file.write_bytes(b"fixture")
    source=f'''import sys;sys.path.insert(0,{str(_AGENT_CORE)!r});from evolution.windows_files import exclusive_file,WorkspaceFileError
try:\n with exclusive_file({str(file)!r},create=False): pass\nexcept WorkspaceFileError as exc:\n print(exc.code);sys.exit(0 if exc.code=="scope_conflict" else 3)\nprint("unexpected-open");sys.exit(4)'''
    with exclusive_file(file,create=False):
        env={key:value for key,value in os.environ.items() if key.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP"}}
        result=subprocess.run([sys.executable,"-c",source],cwd=str(_SANDBOX),env=env,capture_output=True,text=True,timeout=10,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),check=False)
        assert result.returncode==0,result.stderr
        assert result.stdout.strip()=="scope_conflict"
    assert file.read_bytes()==b"fixture"


def test_fixed_worker_uses_private_scope_config_and_one_bounded_message(tmp_path: Path) -> None:
    assert tmp_path.resolve().is_relative_to(_run_root())
    source=tmp_path/"source";state=tmp_path/"state";source.mkdir();state.mkdir()
    scope={"scope_id":"66666666-6666-4666-8666-666666666666","source_root":str(source),"owned_paths":["main.cjs"],"state_root":str(state),"read_only_roots":[]}
    (source/"main.cjs").write_text("fixture\n",encoding="utf-8")
    config=tmp_path/"worker-scope.json";config.write_text(json.dumps(scope,separators=(",",":")),encoding="utf-8")
    env={key:value for key,value in os.environ.items() if key.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP"}}
    env["EVOLUTION_SANDBOX_ROOT"]=str(_SANDBOX)
    env["EVOLUTION_WORKSPACE_CONFIG"]=str(config)
    env["EVOLUTION_WORKSPACE_CONFIG_SHA256"]=hashlib.sha256(config.read_bytes()).hexdigest()
    env["PYTHONDONTWRITEBYTECODE"]="1"
    raw=json.dumps({"operation_id":"11111111-1111-4111-8111-111111111111","action":"inspect","payload":{"query":"summary"}},separators=(",",":"))
    bootstrap=f"import sys;sys.path.insert(0,{str(_AGENT_CORE)!r});from evolution.workspace_worker import main;raise SystemExit(main())"
    result=subprocess.run([sys.executable,"-c",bootstrap],input=raw,text=True,capture_output=True,cwd=str(_SANDBOX),env=env,timeout=10,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),check=False)
    assert result.returncode==0,result.stderr
    assert result.stderr==""
    payload=json.loads(result.stdout)
    assert payload["ok"] is True and payload["scope_id"]==scope["scope_id"]
    assert "source_root" not in payload


def test_synthetic_reparse_attribute_is_rejected_without_creating_host_junction(tmp_path: Path, monkeypatch) -> None:
    if os.name != "nt": pytest.skip("Win32 reparse attributes are Windows-specific")
    assert tmp_path.resolve().is_relative_to(_run_root())
    import evolution.windows_files as windows_files
    parent=tmp_path/"parent";parent.mkdir()
    original=windows_files.os.lstat
    def marked_reparse(path):
        info=original(path)
        if Path(path)==parent:
            class Marked:
                st_file_attributes=getattr(info,"st_file_attributes",0)|0x400
            return Marked()
        return info
    monkeypatch.setattr(windows_files.os,"lstat",marked_reparse)
    with pytest.raises(WorkspaceFileError) as caught:
        with exclusive_file(parent/"file.cjs",create=True):pass
    assert caught.value.code=="scope_conflict"
    assert not (parent/"file.cjs").exists()
