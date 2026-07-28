"""IT-58 / T-1805-07: sidecar log file creation, dual-write, rotation."""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import sys
import tempfile
import unittest
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from server import WsBridge, _build_repl, _run_line, emit_error
from session import create_new
from sidecar_logging import (
    SIDECAR_LOG_BACKUP_COUNT,
    SIDECAR_LOG_MAX_BYTES,
    SIDECAR_LOGGER_NAME,
    configure_sidecar_logging,
    log_sidecar_exception,
    log_sidecar_ws_error,
    sidecar_log_path,
)

from tests.isolation_helpers import make_temp_agent_paths


class SidecarLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.temp_log_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.temp_log_dir, ignore_errors=True))
        self._clear_sidecar_logger()
        patcher = patch("sidecar_logging.sidecar_log_dir", return_value=self.temp_log_dir)
        self.addCleanup(patcher.stop)
        patcher.start()

    def tearDown(self) -> None:
        self._clear_sidecar_logger()

    def _clear_sidecar_logger(self) -> None:
        logger = logging.getLogger(SIDECAR_LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(logging.NOTSET)

    def _log_text(self) -> str:
        path = sidecar_log_path(self.paths)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def test_sidecar_log_path_daily_naming(self) -> None:
        path = sidecar_log_path(self.paths, day=date(2026, 7, 16))
        self.assertEqual(path.name, "sidecar-20260716.log")
        self.assertEqual(path.parent, self.temp_log_dir)

    def test_configure_creates_log_file(self) -> None:
        log_path = configure_sidecar_logging(self.paths)
        self.assertTrue(log_path.is_file())
        self.assertIn("sidecar logging ready", self._log_text())

    def test_configure_uses_rotating_handler(self) -> None:
        configure_sidecar_logging(self.paths)
        logger = logging.getLogger(SIDECAR_LOGGER_NAME)
        self.assertEqual(len(logger.handlers), 1)
        handler = logger.handlers[0]
        self.assertIsInstance(handler, RotatingFileHandler)
        self.assertEqual(handler.maxBytes, SIDECAR_LOG_MAX_BYTES)
        self.assertEqual(handler.backupCount, SIDECAR_LOG_BACKUP_COUNT)

    def test_configure_is_idempotent_for_same_path(self) -> None:
        first = configure_sidecar_logging(self.paths)
        second = configure_sidecar_logging(self.paths)
        self.assertEqual(first, second)
        self.assertEqual(len(logging.getLogger(SIDECAR_LOGGER_NAME).handlers), 1)

    def test_emit_error_dual_writes(self) -> None:
        configure_sidecar_logging(self.paths)
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=self.paths)
        marker = f"IT-58 emit_error {secrets.token_hex(4)}"

        emit_error(bridge, marker)

        self.assertEqual(events, [{"type": "error", "message": marker}])
        self.assertIn(f"ws error: {marker}", self._log_text())

    def test_output_fn_repl_error_dual_writes(self) -> None:
        configure_sidecar_logging(self.paths)
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=self.paths)

        bridge.output_fn("error: IT-58 repl timeout")

        self.assertEqual(events[0]["type"], "error")
        self.assertIn("ws error: error: IT-58 repl timeout", self._log_text())

    def test_log_sidecar_exception_writes_traceback(self) -> None:
        configure_sidecar_logging(self.paths)
        try:
            raise RuntimeError("IT-58 traceback probe")
        except RuntimeError as exc:
            log_sidecar_exception("_run_line failed line='probe'", exc)

        text = self._log_text()
        self.assertIn("IT-58 traceback probe", text)
        self.assertIn("Traceback (most recent call last)", text)

    def test_run_line_exception_dual_writes(self) -> None:
        configure_sidecar_logging(self.paths)
        events: list[dict[str, Any]] = []
        bridge = WsBridge(emit=events.append, paths=self.paths)
        session = create_new(self.paths, conversation_id=f"_it58_{secrets.token_hex(4)}")
        repl = _build_repl(session, self.paths, bridge)
        marker = "IT-58 run_line probe"

        def boom(_line: str) -> str:
            raise RuntimeError(marker)

        repl.handle_line = boom  # type: ignore[method-assign]

        asyncio.run(_run_line(repl, bridge, "probe", self.paths))

        text = self._log_text()
        self.assertIn(marker, text)
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn(f"ws error: {marker}", text)
        self.assertTrue(any(event.get("type") == "error" for event in events))

    def test_rotation_creates_backup_files(self) -> None:
        with patch("sidecar_logging.SIDECAR_LOG_MAX_BYTES", 256), patch(
            "sidecar_logging.SIDECAR_LOG_BACKUP_COUNT", 2
        ):
            configure_sidecar_logging(self.paths)
            logger = logging.getLogger(SIDECAR_LOGGER_NAME)
            for _ in range(40):
                logger.info("x" * 80)

        files = sorted(self.temp_log_dir.glob("sidecar-*.log*"))
        self.assertGreaterEqual(len(files), 2)


if __name__ == "__main__":
    unittest.main()
