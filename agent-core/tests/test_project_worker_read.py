from pathlib import Path
import os
import subprocess
import pytest

from project_worker_read import ProjectTextReader, ProjectReadError


@pytest.mark.parametrize('name', ['', '.', '../b/x', 'a/../x', '/absolute', r'C:\\outside', r'\\server\share\x', 'host/x', 'file:stream', 'CON.txt', 'trailing.', 'trailing ', 'a//b', r'a\b', 'bad\x00name'])
def test_unsafe_names_rejected_before_read(tmp_path, name):
    reader=ProjectTextReader(tmp_path)
    with pytest.raises(ProjectReadError): reader.read_texts([name])


def test_platform_fails_closed_no_regular_file_or_foreign_read(tmp_path):
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    (a/'ok.txt').write_text('project A',encoding='utf-8'); (b/'secret.txt').write_text('sentinel B',encoding='utf-8')
    reader=ProjectTextReader(a)
    if os.name == 'nt':
        assert reader.read_texts(['ok.txt']) == {'ok.txt':'project A'}
    else:
        with pytest.raises(ProjectReadError): reader.read_texts(['ok.txt'])
    with pytest.raises(ProjectReadError): reader.read_texts(['../b/secret.txt'])
    assert (b/'secret.txt').read_text(encoding='utf-8')=='sentinel B'


def test_limits_fail_closed(tmp_path):
    reader=ProjectTextReader(tmp_path)
    for request in ([], ['x']*33, 'x', [1]):
        with pytest.raises(ProjectReadError): reader.read_texts(request)


@pytest.mark.skipif(os.name!='nt',reason='real Windows junction')
def test_real_windows_junction_is_not_traversed(tmp_path):
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    (b/'secret.txt').write_text('sentinel B',encoding='utf-8')
    link=a/'junction'
    assert link.is_absolute() and link.parent.resolve()==a.resolve() and b.resolve().parent==tmp_path.resolve()
    subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
        'New-Item -ItemType Junction -Path $env:TEST_JUNCTION -Target $env:TEST_TARGET -ErrorAction Stop | Out-Null'],
        env={**os.environ, 'TEST_JUNCTION':str(link), 'TEST_TARGET':str(b)}, check=True,capture_output=True)
    try:
        with pytest.raises(ProjectReadError): ProjectTextReader(a).read_texts(['junction/secret.txt'])
        assert (b/'secret.txt').read_text(encoding='utf-8')=='sentinel B'
    finally:
        assert link.is_absolute() and link.parent.resolve()==a.resolve()
        os.rmdir(link)
import threading

@pytest.mark.skipif(os.name != 'nt', reason='Windows native handles')
def test_windows_reads_text_and_enforces_file_limits(tmp_path):
    a=tmp_path/'a'; a.mkdir(); (a/'note.txt').write_text('hello 世界',encoding='utf-8')
    (a/'big.txt').write_bytes(b'x'*(1024*1024+1)); (a/'binary.txt').write_bytes(b'x\x00y')
    reader=ProjectTextReader(a)
    assert reader.read_texts(['note.txt']) == {'note.txt':'hello 世界'}
    for name in ('big.txt','binary.txt'):
        with pytest.raises(ProjectReadError): reader.read_texts([name])

@pytest.mark.skipif(os.name != 'nt', reason='Windows native handles')
def test_windows_hardlink_rejected(tmp_path):
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    source=b/'secret.txt'; source.write_text('sentinel',encoding='utf-8')
    os.link(source,a/'linked.txt')
    with pytest.raises(ProjectReadError): ProjectTextReader(a).read_texts(['linked.txt'])
    assert source.read_text(encoding='utf-8')=='sentinel'

@pytest.mark.skipif(os.name != 'nt', reason='Windows native handles')
def test_relative_directory_replacement_detected(tmp_path, monkeypatch):
    import project_worker_read as module
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir(); sub=a/'sub'; sub.mkdir()
    (sub/'x.txt').write_text('A'); (b/'x.txt').write_text('B')
    reader=ProjectTextReader(a); opened=threading.Event(); resume=threading.Event(); original=module._open_child
    def hook(parent,name,*,directory):
        handle=original(parent,name,directory=directory)
        if name=='sub' and directory and not opened.is_set(): opened.set(); assert resume.wait(8)
        return handle
    monkeypatch.setattr(module,'_open_child',hook)
    result=[]
    def run():
        try: result.append(reader.read_texts(['sub/x.txt']))
        except Exception as exc: result.append(exc)
    thread=threading.Thread(target=run); thread.start(); link_created=False
    try:
        assert opened.wait(8)
        assert sub.is_absolute() and sub.parent.resolve()==a.resolve() and b.parent.resolve()==tmp_path.resolve()
        os.rename(sub,a/'moved')
        subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
            'New-Item -ItemType Junction -Path $env:TEST_JUNCTION -Target $env:TEST_TARGET -ErrorAction Stop | Out-Null'],
            env={**os.environ,'TEST_JUNCTION':str(sub),'TEST_TARGET':str(b)},check=True,capture_output=True)
        link_created=True
    finally:
        resume.set(); thread.join(8)
        if link_created:
            assert sub.is_absolute() and sub.parent.resolve()==a.resolve()
            os.rmdir(sub)
    assert not thread.is_alive() and len(result)==1 and isinstance(result[0],ProjectReadError)
    assert (b/'x.txt').read_text()=='B'

@pytest.mark.skipif(os.name != 'nt', reason='Windows native handles')
def test_root_binding_replacement_fails_closed(tmp_path):
    root=tmp_path/'project'; root.mkdir(); (root/'x.txt').write_text('A')
    reader=ProjectTextReader(root)
    os.rename(root,tmp_path/'old'); root.mkdir(); (root/'x.txt').write_text('B')
    with pytest.raises(ProjectReadError): reader.read_texts(['x.txt'])

@pytest.mark.skipif(os.name != 'nt', reason='Windows native handles')
def test_final_component_junction_rejected(tmp_path):
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir(); (b/'x.txt').write_text('B')
    link=a/'link'
    assert link.is_absolute() and link.parent.resolve()==a.resolve() and b.parent.resolve()==tmp_path.resolve()
    subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
        'New-Item -ItemType Junction -Path $env:TEST_JUNCTION -Target $env:TEST_TARGET -ErrorAction Stop | Out-Null'],
        env={**os.environ,'TEST_JUNCTION':str(link),'TEST_TARGET':str(b)},check=True,capture_output=True)
    try:
        with pytest.raises(ProjectReadError): ProjectTextReader(a).read_texts(['link'])
    finally:
        assert link.is_absolute() and link.parent.resolve()==a.resolve()
        os.rmdir(link)
