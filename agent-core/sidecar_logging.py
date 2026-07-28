"""Sidecar log path resolution and FileHandler setup (STABILIZATION T-1805).

T-1805-01: :func:`sidecar_log_path`
T-1805-02: :func:`configure_sidecar_logging`
T-1805-05: ``RotatingFileHandler`` at :data:`SIDECAR_LOG_MAX_BYTES`
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from paths import AgentPaths

SIDECAR_LOG_DIRNAME = "logs"
SIDECAR_LOG_PREFIX = "sidecar-"
SIDECAR_LOGGER_NAME = "my_agent.sidecar"
SIDECAR_LOG_MAX_BYTES = 10 * 1024 * 1024
SIDECAR_LOG_BACKUP_COUNT = 5


def sidecar_log_dir(paths: AgentPaths) -> Path:
    """``<agent_root>/data/logs/`` — created on demand by T-1805-02."""
    return paths.data / SIDECAR_LOG_DIRNAME


def sidecar_log_path(paths: AgentPaths, *, day: date | None = None) -> Path:
    """Daily log file: ``data/logs/sidecar-YYYYMMDD.log`` (local calendar date)."""
    stamp = (day or datetime.now().astimezone().date()).strftime("%Y%m%d")
    return sidecar_log_dir(paths) / f"{SIDECAR_LOG_PREFIX}{stamp}.log"


def configure_sidecar_logging(paths: AgentPaths) -> Path:
    """Mount UTF-8 rotating ``FileHandler`` on ``SIDECAR_LOGGER_NAME``; create ``data/logs/``."""
    log_path = sidecar_log_path(paths)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(SIDECAR_LOGGER_NAME)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)

    resolved = log_path.resolve()
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            if Path(handler.baseFilename).resolve() == resolved:
                return log_path
            logger.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        log_path,
        mode="a",
        maxBytes=SIDECAR_LOG_MAX_BYTES,
        backupCount=SIDECAR_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False

    logger.info("sidecar logging ready -> %s", log_path.as_posix())
    return log_path


def log_sidecar_exception(message: str, exc: BaseException) -> None:
    """Write exception + traceback to the sidecar log file."""
    logging.getLogger(SIDECAR_LOGGER_NAME).error("%s: %s", message, exc, exc_info=exc)


def log_sidecar_ws_error(message: str) -> None:
    """Mirror a WS ``error`` event payload to the sidecar log file."""
    logging.getLogger(SIDECAR_LOGGER_NAME).error("ws error: %s", message)


def _demo() -> None:
    paths = AgentPaths.discover()
    p = sidecar_log_path(paths, day=date(2026, 7, 16))
    assert p.name == "sidecar-20260716.log"
    assert p.parent == paths.data / "logs"
    print(f"[PASS] sidecar_log_path -> {p.as_posix()}")

    configured = configure_sidecar_logging(paths)
    assert configured == sidecar_log_path(paths)
    assert configured.is_file()
    handler = logging.getLogger(SIDECAR_LOGGER_NAME).handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == SIDECAR_LOG_MAX_BYTES
    assert handler.backupCount == SIDECAR_LOG_BACKUP_COUNT
    print(f"[PASS] configure_sidecar_logging -> {configured.as_posix()} (rotate {SIDECAR_LOG_MAX_BYTES}B)")


if __name__ == "__main__":
    _demo()
