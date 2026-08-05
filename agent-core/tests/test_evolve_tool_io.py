"""Tests for evolve tool stdout JSON protocol (Windows GBK-safe)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from evolve_tool_io import emit_json, run_tool_main


def test_emit_json_writes_utf8_bytes() -> None:
    mock_stdout = MagicMock()
    with patch("evolve_tool_io.sys.stdout", mock_stdout):
        emit_json({"ok": True, "msg": "中文测试"})
    raw = mock_stdout.buffer.write.call_args[0][0]
    assert raw.endswith(b"\n")
    parsed = json.loads(raw.decode("utf-8").strip())
    assert parsed["msg"] == "中文测试"


def test_run_tool_main_exits_on_failure() -> None:
    def fail_fn(_payload: dict) -> dict:
        return {"ok": False, "error": "失败"}

    mock_stdout = MagicMock()
    with (
        patch("evolve_tool_io.sys.stdin", __import__("io").StringIO('{"dry_run": true}')),
        patch("evolve_tool_io.sys.stdout", mock_stdout),
        pytest.raises(SystemExit) as exc,
    ):
        run_tool_main(fail_fn)
    assert exc.value.code == 1
    raw = mock_stdout.buffer.write.call_args[0][0]
    parsed = json.loads(raw.decode("utf-8").strip())
    assert parsed["error"] == "失败"


def test_unicode_tool_protocol_via_subprocess(tmp_path: Path) -> None:
    """End-to-end stdin JSON → stdout JSON (replaces archived repl probe)."""
    tool_dir = tmp_path / "unicode_probe"
    tool_dir.mkdir()
    probe = tool_dir / "main.py"
    probe.write_text(
        f"""import sys
from pathlib import Path

core = Path({str(_AGENT_CORE)!r})
if str(core) not in sys.path:
    sys.path.insert(0, str(core))
from evolve_tool_io import run_tool_main


def run(payload: dict) -> dict:
    return {{"ok": True, "stdout": "你好"}}


if __name__ == "__main__":
    run_tool_main(run)
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(probe)],
        input=json.dumps({"dry_run": True}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env={**dict(__import__("os").environ), "PYTHONUTF8": "0"},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.strip())
    assert data["ok"] is True
    assert "你好" in data.get("stdout", "")
