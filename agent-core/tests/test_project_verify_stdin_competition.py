from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_requirement_pytest_does_not_inherit_competing_stdin(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'test_sample.py').write_text('def test_ok(): assert True\n', encoding='utf-8')
    result_file = tmp_path / 'result.json'
    core = Path(__file__).resolve().parents[1]
    script = tmp_path / 'parent.py'
    script.write_text(
        "import json, os, sys, threading\n"
        "from pathlib import Path\n"
        "from threading import Event\n"
        f"sys.path.insert(0, {str(core)!r})\n"
        "from project_verify import run_requirement_pytest\n"
        "ready = Event()\n"
        "def reader():\n"
        "    ready.set()\n"
        "    sys.stdin.buffer.readline()\n"
        "threading.Thread(target=reader, daemon=True).start()\n"
        "ready.wait()\n"
        f"result = run_requirement_pytest(Path({str(project)!r}), 'test_sample.py::test_ok', cancel_event=Event(), timeout_sec=5)\n"
        f"Path({str(result_file)!r}).write_text(json.dumps([result.reported_pass, result.exit_code, result.reason]), encoding='utf-8')\n"
        "os._exit(0)\n", encoding='utf-8')
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    with (tmp_path / 'parent-output').open('wb') as output:
        parent = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                                  stdout=output, stderr=output, env=env)
        try:
            parent.wait(timeout=20)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            parent.stdin.close()
    assert parent.returncode == 0, (tmp_path / 'parent-output').read_text(errors='replace')
    assert json.loads(result_file.read_text(encoding='utf-8'))[:2] == [True, 0]
