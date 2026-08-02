"""browser_open — open URL in the system default browser (Phase 33 F1).

Loopback http(s) may skip confirm (executor); external URLs always confirm.
No Playwright / headless (F2 defer).
"""

from __future__ import annotations

import ipaddress
import sys
import webbrowser
from typing import Any
from urllib.parse import urlparse


def _agent_root():
    from pathlib import Path

    current = Path(__file__).resolve().parent
    for directory in (current, *current.parents):
        evolve_marker = directory / "evolve"
        if (evolve_marker / "_index.core.toml").is_file() or (evolve_marker / "_index.toml").is_file():
            return directory
    raise RuntimeError("could not locate agent root")


def is_loopback_url(url: str) -> bool:
    """True when hostname is localhost / loopback IP."""
    try:
        host = urlparse(url).hostname
    except Exception:
        return False
    if not host:
        return False
    lowered = host.lower().strip("[]")
    if lowered in {"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"}:
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def needs_confirm(url: str) -> bool:
    """F1: confirm unless loopback http(s)."""
    return not is_loopback_url(url)


def _normalize_url(raw: str) -> str:
    text = raw.strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("url missing host")
    # Block credentials in URL to reduce phishing footguns in confirm preview
    if parsed.username or parsed.password:
        raise ValueError("url must not contain userinfo (user:pass@)")
    return text


def browser_open(payload: dict[str, Any]) -> dict[str, Any]:
    url_raw = payload.get("url")
    if not isinstance(url_raw, str) or not url_raw.strip():
        return {"ok": False, "error": "url is required"}

    try:
        url = _normalize_url(url_raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    dry_run = bool(payload.get("dry_run", False))
    loopback = is_loopback_url(url)
    out: dict[str, Any] = {
        "ok": True,
        "url": url,
        "loopback": loopback,
        "needs_confirm": needs_confirm(url),
    }

    if dry_run:
        out["dry_run"] = True
        out["would_open"] = True
        return out

    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:
        return {"ok": False, "error": f"failed to open browser: {exc}", "url": url}

    out["opened"] = bool(opened)
    if not opened:
        # webbrowser.open may return False on some platforms even when a handler ran
        out["warning"] = "webbrowser.open returned False; check default browser"
    return out


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(browser_open)


if __name__ == "__main__":
    main()
