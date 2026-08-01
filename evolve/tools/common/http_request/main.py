"""http_request — HTTP probe / API call (Phase 26 M0).

Loopback GET/HEAD skip confirm (executor); other methods or non-localhost need confirm.
"""

from __future__ import annotations

import ipaddress
import json
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_MAX_TIMEOUT = 60
_DEFAULT_TIMEOUT = 15
_MAX_BODY_CHARS = 65_536
_DEFAULT_BODY_CHARS = 32_768
_MAX_HEADER_CHARS = 4_096
_ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
_SAFE_METHODS = frozenset({"GET", "HEAD"})


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


def needs_confirm(method: str, url: str) -> bool:
    """D2: confirm unless loopback AND safe method."""
    m = (method or "GET").strip().upper()
    if m not in _SAFE_METHODS:
        return True
    return not is_loopback_url(url)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(truncated)", True


def _normalize_url(raw: str) -> str:
    text = raw.strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("url missing host")
    return text


def _build_body(payload: dict[str, Any]) -> tuple[bytes | None, dict[str, str]]:
    extra_headers: dict[str, str] = {}
    if "json" in payload and payload.get("json") is not None:
        data = json.dumps(payload["json"], ensure_ascii=False).encode("utf-8")
        extra_headers["Content-Type"] = "application/json; charset=utf-8"
        return data, extra_headers
    body = payload.get("body")
    if body is None or body == "":
        return None, extra_headers
    if not isinstance(body, str):
        raise ValueError("body must be a string")
    return body.encode("utf-8"), extra_headers


def http_request(payload: dict[str, Any]) -> dict[str, Any]:
    url_raw = payload.get("url")
    if not isinstance(url_raw, str) or not url_raw.strip():
        return {"ok": False, "error": "url is required"}

    try:
        url = _normalize_url(url_raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    method = str(payload.get("method") or "GET").strip().upper()
    if method not in _ALLOWED_METHODS:
        return {"ok": False, "error": f"method must be one of {sorted(_ALLOWED_METHODS)}"}

    timeout = payload.get("timeout_sec", _DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        return {"ok": False, "error": "timeout_sec must be an integer"}
    timeout = max(1, min(timeout, _MAX_TIMEOUT))

    max_chars = payload.get("max_body_chars", _DEFAULT_BODY_CHARS)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        return {"ok": False, "error": "max_body_chars must be an integer"}
    max_chars = max(1, min(max_chars, _MAX_BODY_CHARS))

    headers: dict[str, str] = {"User-Agent": "my-agent-http_request/1.0"}
    raw_headers = payload.get("headers")
    if raw_headers is not None:
        if not isinstance(raw_headers, dict):
            return {"ok": False, "error": "headers must be an object"}
        for key, value in raw_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return {"ok": False, "error": "headers values must be strings"}
            headers[key] = value

    try:
        body_bytes, body_headers = _build_body(payload)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    headers.update(body_headers)

    if method in _SAFE_METHODS:
        body_bytes = None

    req = Request(url, data=body_bytes, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — intentional HTTP tool
            status = int(getattr(resp, "status", None) or resp.getcode())
            resp_headers = {k: v for k, v in resp.headers.items()}
            raw = b"" if method == "HEAD" else resp.read()
    except HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        raw = exc.read() if exc.fp is not None else b""
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        text, trunc = _truncate(text, max_chars)
        hdrs = {k: v for k, v in (exc.headers.items() if exc.headers else [])}
        hdr_text = json.dumps(hdrs, ensure_ascii=False)
        hdr_text, hdr_trunc = _truncate(hdr_text, _MAX_HEADER_CHARS)
        return {
            "ok": False,
            "error": f"HTTP {exc.code}",
            "status_code": int(exc.code),
            "url": url,
            "method": method,
            "headers": json.loads(hdr_text) if not hdr_trunc else hdr_text,
            "headers_truncated": hdr_trunc,
            "body": text,
            "truncated": trunc,
            "elapsed_ms": elapsed,
            "loopback": is_loopback_url(url),
        }
    except URLError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "error": f"network error: {exc.reason}",
            "url": url,
            "method": method,
            "elapsed_ms": elapsed,
            "loopback": is_loopback_url(url),
        }
    except TimeoutError:
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "error": "request timed out",
            "url": url,
            "method": method,
            "elapsed_ms": elapsed,
            "loopback": is_loopback_url(url),
        }
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "error": str(exc),
            "url": url,
            "method": method,
            "elapsed_ms": elapsed,
            "loopback": is_loopback_url(url),
        }

    elapsed = int((time.perf_counter() - started) * 1000)
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = ""
    text, trunc = _truncate(text, max_chars)
    hdr_text = json.dumps(resp_headers, ensure_ascii=False)
    hdr_text, hdr_trunc = _truncate(hdr_text, _MAX_HEADER_CHARS)
    return {
        "ok": 200 <= status < 400,
        "status_code": status,
        "url": url,
        "method": method,
        "headers": json.loads(hdr_text) if not hdr_trunc else hdr_text,
        "headers_truncated": hdr_trunc,
        "body": text,
        "truncated": trunc,
        "elapsed_ms": elapsed,
        "loopback": is_loopback_url(url),
        "error": None if 200 <= status < 400 else f"HTTP {status}",
    }


def main() -> None:
    core = _agent_root() / "agent-core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from evolve_tool_io import run_tool_main

    run_tool_main(http_request)


if __name__ == "__main__":
    main()
