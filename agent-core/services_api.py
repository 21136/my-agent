"""WebSocket API for managed services panel (Phase 27 EXEC-OBSERVABILITY).

Read-only list/logs via evolve `run_service` — no confirm, no mutating actions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths

# Desktop sidebar defaults (EXEC-OBSERVABILITY D3).
DEFAULT_TAIL_LINES = 40
DEFAULT_MAX_CHARS = 4096


class ServicesApiError(ValueError):
    """Raised for invalid services.* client messages."""


def _load_run_service(paths: AgentPaths):
    main_py = paths.evolve / "tools" / "common" / "run_service" / "main.py"
    if not main_py.is_file():
        raise ServicesApiError("run_service tool not found")
    spec = importlib.util.spec_from_file_location("run_service_services_api", main_py)
    if spec is None or spec.loader is None:
        raise ServicesApiError("failed to load run_service")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clamp_log_text(text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def dispatch_services_message(paths: AgentPaths, message: dict[str, Any]) -> dict[str, Any]:
    """Handle services.list / services.logs → ServerEvent dict."""
    msg_type = message.get("type")
    if msg_type == "services.list":
        mod = _load_run_service(paths)
        out = mod.run_service({"action": "list"})
        if not out.get("ok"):
            raise ServicesApiError(str(out.get("error") or "services.list failed"))
        services = out.get("services") or []
        if not isinstance(services, list):
            services = []
        return {"type": "services.list.done", "ok": True, "services": services}

    if msg_type == "services.logs":
        name = message.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ServicesApiError("services.logs requires name")
        tail_lines = message.get("tail_lines", DEFAULT_TAIL_LINES)
        try:
            tail_lines = int(tail_lines)
        except (TypeError, ValueError):
            tail_lines = DEFAULT_TAIL_LINES
        tail_lines = max(1, min(tail_lines, 200))
        mod = _load_run_service(paths)
        out = mod.run_service(
            {"action": "logs", "name": name.strip(), "tail_lines": tail_lines}
        )
        if not out.get("ok"):
            raise ServicesApiError(str(out.get("error") or "services.logs failed"))
        text = _clamp_log_text(str(out.get("text") or ""))
        return {
            "type": "services.logs.done",
            "ok": True,
            "name": out.get("name") or name.strip(),
            "log_path": out.get("log_path"),
            "text": text,
        }

    raise ServicesApiError(f"unknown services message: {msg_type}")
