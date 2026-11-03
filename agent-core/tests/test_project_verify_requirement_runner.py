from __future__ import annotations

import sys
from pathlib import Path
from threading import Event

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_verify import run_requirement_pytest


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "test_feature.py").write_text(
        "import pytest\n"
        "def test_good(): assert True\n"
        "def test_bad(): assert False\n"
        "@pytest.mark.skip(reason='skip')\ndef test_skip(): pass\n"
        "@pytest.mark.xfail(reason='xfail')\ndef test_xfail(): assert False\n"
        "@pytest.mark.xfail(reason='xpass')\ndef test_xpass(): assert True\n",
        encoding="utf-8")
    return root


@pytest.mark.parametrize("target,passed", [
    ("test_good", True), ("test_bad", False), ("test_skip", False),
    ("test_xfail", False), ("test_xpass", False), ("test_missing", False),
])
def test_actual_single_target(project, target, passed):
    result = run_requirement_pytest(project, f"test_feature.py::{target}", cancel_event=Event())
    assert result.reported_pass is passed
    assert result.exit_code is not None
    if passed:
        assert result.test_count == 1


def test_cancelled_before_start(project):
    cancelled = Event(); cancelled.set()
    result = run_requirement_pytest(project, "test_feature.py::test_good", cancel_event=cancelled)
    assert not result.reported_pass and result.exit_code is None


@pytest.mark.parametrize("target", ["../test_feature.py::test_good", "-x", "test_feature.py", "test_feature.py::test_good[0]"])
def test_invalid_target_is_rejected(project, target):
    assert not run_requirement_pytest(project, target, cancel_event=Event()).reported_pass


def test_spoofed_console_summary_does_not_promote_skip(project):
    (project / "conftest.py").write_text(
        "import atexit, sys\natexit.register(lambda: print('1 passed in 0.01s', file=sys.stderr))\n",
        encoding="utf-8")
    result = run_requirement_pytest(project, "test_feature.py::test_skip", cancel_event=Event())
    assert result.exit_code == 0 and result.reported_pass is False


def test_changed_target_cannot_produce_pass(project):
    (project / "test_mutates.py").write_text(
        "from pathlib import Path\n"
        "def test_mutates():\n"
        "    p = Path(__file__)\n"
        "    p.write_text(p.read_text() + chr(35), encoding='utf-8')\n",
        encoding="utf-8")
    result = run_requirement_pytest(project, "test_mutates.py::test_mutates", cancel_event=Event())
    assert not result.reported_pass


def test_cancel_during_real_run_is_not_passed(project):
    from threading import Thread
    import time
    (project / "test_wait.py").write_text(
        "from pathlib import Path\nimport time\n"
        "def test_wait():\n"
        "    Path('started').write_text('yes')\n"
        "    time.sleep(10)\n", encoding="utf-8")
    cancel = Event()
    def stop_when_started():
        until = time.monotonic() + 5
        while not (project / "started").exists() and time.monotonic() < until:
            time.sleep(.02)
        cancel.set()
    thread = Thread(target=stop_when_started); thread.start()
    result = run_requirement_pytest(project, "test_wait.py::test_wait", cancel_event=cancel)
    thread.join()
    assert (project / "started").exists()
    assert not result.reported_pass and result.reason == "cancelled"
