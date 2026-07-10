"""Builtin fetch_url (TOOLS.md §7.5, TASKS T-104c)."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tools.http_client import make_httpx_client
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

TOOL_NAME = "fetch_url"
DEFAULT_MAX_CHARS = 32_000
HARD_MAX_CHARS = 128_000
DEFAULT_MAX_BYTES = 2_097_152
DEFAULT_TIMEOUT_SEC = 15.0
DEFAULT_USER_AGENT = "my-agent/1.0"
MAX_REDIRECTS = 5
# Clash/Mihomo fake-ip pool (RFC 2544 benchmark range); not a real SSRF target.
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")

_RAW_MEDIA_PREFIXES = (
    "text/plain",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
)
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.google",
    }
)


@dataclass(frozen=True, slots=True)
class FetchUrlConfig:
    timeout_sec: float
    max_bytes: int
    user_agent: str
    default_max_chars: int


def run(arguments: dict[str, Any], *, paths: Any | None = None) -> ToolResult:
    """Fetch a public HTTP(S) URL and return text content."""
    _ = paths
    started = time.perf_counter()
    config = _load_config()

    url_arg = arguments.get("url")
    if not isinstance(url_arg, str) or not url_arg.strip():
        return _fail("url is required", ToolErrorCode.VALIDATION_ERROR, started)

    max_chars = arguments.get("max_chars", config.default_max_chars)
    if not isinstance(max_chars, int) or max_chars < 1:
        return _fail("max_chars must be a positive integer", ToolErrorCode.VALIDATION_ERROR, started)
    max_chars = min(max_chars, HARD_MAX_CHARS)

    try:
        request_url = _normalize_http_url(url_arg.strip())
    except _InvalidUrlError as exc:
        return _fail(str(exc), ToolErrorCode.INVALID_URL, started, url=url_arg)

    try:
        final_url, raw_body, content_type = _fetch_bytes(request_url, config=config)
    except _BlockedHostError as exc:
        return _fail(str(exc), ToolErrorCode.BLOCKED_HOST, started, url=url_arg)
    except _InvalidUrlError as exc:
        return _fail(str(exc), ToolErrorCode.INVALID_URL, started, url=url_arg)
    except _UnsupportedContentTypeError as exc:
        return _fail(
            str(exc),
            ToolErrorCode.UNSUPPORTED_CONTENT_TYPE,
            started,
            url=url_arg,
            details={"content_type": exc.content_type},
        )
    except httpx.TimeoutException:
        return _fail("fetch timed out", ToolErrorCode.TIMEOUT, started, url=url_arg)
    except httpx.HTTPError as exc:
        return _fail(str(exc), ToolErrorCode.NETWORK_ERROR, started, url=url_arg)
    except _FetchHttpError as exc:
        return _fail(
            str(exc),
            ToolErrorCode.HTTP_ERROR,
            started,
            url=url_arg,
            details={"status_code": exc.status_code},
        )

    truncated = len(raw_body) > config.max_bytes
    body = raw_body[: config.max_bytes]

    try:
        text = _body_to_text(body, content_type)
    except _UnsupportedContentTypeError as exc:
        return _fail(
            str(exc),
            ToolErrorCode.UNSUPPORTED_CONTENT_TYPE,
            started,
            url=url_arg,
            details={"content_type": exc.content_type},
        )

    if len(text) > max_chars:
        truncated = True
        text = text[:max_chars]

    return tool_ok(
        TOOL_NAME,
        {
            "url": request_url,
            "final_url": final_url,
            "content": text,
            "content_type": content_type.split(";")[0].strip().lower(),
        },
        truncated=truncated,
        duration_ms=_elapsed_ms(started),
    )


class _FetchError(ValueError):
    pass


class _InvalidUrlError(_FetchError):
    pass


class _BlockedHostError(_FetchError):
    pass


class _UnsupportedContentTypeError(_FetchError):
    def __init__(self, message: str, *, content_type: str) -> None:
        super().__init__(message)
        self.content_type = content_type


class _FetchHttpError(_FetchError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _load_config() -> FetchUrlConfig:
    timeout_raw = os.environ.get("FETCH_URL_TIMEOUT_SEC", str(DEFAULT_TIMEOUT_SEC))
    max_bytes_raw = os.environ.get("FETCH_URL_MAX_BYTES", str(DEFAULT_MAX_BYTES))
    default_chars_raw = os.environ.get("FETCH_URL_MAX_CHARS_DEFAULT", str(DEFAULT_MAX_CHARS))

    try:
        timeout_sec = float(timeout_raw)
        max_bytes = int(max_bytes_raw)
        default_max_chars = int(default_chars_raw)
    except ValueError as exc:
        raise _FetchError("invalid fetch_url environment configuration") from exc

    return FetchUrlConfig(
        timeout_sec=timeout_sec,
        max_bytes=max_bytes,
        user_agent=os.environ.get("FETCH_URL_USER_AGENT", DEFAULT_USER_AGENT),
        default_max_chars=default_max_chars,
    )


def _normalize_http_url(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise _InvalidUrlError("only http:// and https:// URLs are allowed")
    if not parsed.netloc:
        raise _InvalidUrlError("url must include a host")
    return parsed.geturl()


def _fetch_bytes(url: str, *, config: FetchUrlConfig) -> tuple[str, bytes, str]:
    headers = {"User-Agent": config.user_agent, "Accept": "*/*"}
    current = url

    with make_httpx_client(timeout=config.timeout_sec) as client:
        for hop in range(MAX_REDIRECTS + 1):
            _assert_host_allowed(urlparse(current).hostname or "")
            response = client.get(current, headers=headers, follow_redirects=False)
            if response.status_code >= 400:
                raise _FetchHttpError(
                    f"HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            if response.is_redirect:
                if hop >= MAX_REDIRECTS:
                    raise _InvalidUrlError("too many redirects")
                location = response.headers.get("location")
                if not location:
                    raise _FetchHttpError("redirect without Location header", status_code=response.status_code)
                current = _normalize_http_url(urljoin(current, location))
                continue

            content_type_header = response.headers.get("content-type", "application/octet-stream")
            raw_body = _read_limited(response, limit=config.max_bytes + 1)
            return current, raw_body, content_type_header

    raise _InvalidUrlError("too many redirects")


def _read_limited(response: httpx.Response, *, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        chunks.append(chunk)
        if total > limit:
            break
    return b"".join(chunks)


def _assert_host_allowed(hostname: str) -> None:
    host = hostname.strip().lower().rstrip(".")
    if not host:
        raise _InvalidUrlError("url must include a host")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise _BlockedHostError(f"blocked host: {host}")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if _is_blocked_ip(ip):
            raise _BlockedHostError(f"blocked host: {host}")
        return

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _BlockedHostError(f"could not resolve host: {host}") from exc

    if not infos:
        raise _BlockedHostError(f"could not resolve host: {host}")

    for info in infos:
        address = info[4][0]
        ip = ipaddress.ip_address(address)
        if _is_blocked_ip(ip):
            raise _BlockedHostError(f"blocked host: {host}")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 4 and ip in _FAKE_IP_NETWORK:
        return False
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def _body_to_text(body: bytes, content_type: str) -> str:
    media_type = content_type.split(";")[0].strip().lower()
    if media_type == "text/html" or media_type == "application/xhtml+xml":
        charset = _charset_from_content_type(content_type)
        html = body.decode(charset, errors="replace")
        return _html_to_text(html)

    if media_type.startswith("image/") or media_type == "application/pdf":
        raise _UnsupportedContentTypeError(
            f"unsupported content type: {media_type}",
            content_type=media_type,
        )

    if media_type.startswith("text/") or media_type in _RAW_MEDIA_PREFIXES:
        charset = _charset_from_content_type(content_type)
        return body.decode(charset, errors="replace")

    raise _UnsupportedContentTypeError(
        f"unsupported content type: {media_type}",
        content_type=media_type,
    )


def _charset_from_content_type(content_type: str) -> str:
    message = EmailMessage()
    message["content-type"] = content_type
    charset = message.get_content_charset()
    return charset or "utf-8"


class _HTMLToText(HTMLParser):
    _BLOCK_TAGS = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})
    _SKIP_TAGS = frozenset({"script", "style", "head", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        name = tag.lower()
        if name in self._SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and name in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._skip_depth == 0 and name in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        text = "".join(self._chunks)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    parser = _HTMLToText()
    parser.feed(html)
    parser.close()
    return parser.get_text()


def _fail(
    message: str,
    code: str,
    started: float,
    *,
    url: str | None = None,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    merged = dict(details or {})
    if url is not None:
        merged.setdefault("url", url)
    return tool_fail(
        TOOL_NAME,
        code,
        message,
        duration_ms=_elapsed_ms(started),
        details=merged or None,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _demo() -> None:
    cases: list[tuple[dict[str, Any], bool, str]] = [
        ({}, False, "missing url"),
        ({"url": "ftp://example.com"}, False, "invalid scheme"),
        ({"url": "http://127.0.0.1"}, False, "blocked loopback"),
        ({"url": "http://localhost"}, False, "blocked localhost"),
        ({"url": "https://example.com", "max_chars": 0}, False, "bad max_chars"),
    ]

    for arguments, should_ok, label in cases:
        result = run(arguments)
        if result.ok != should_ok:
            print(f"[FAIL] {label}: expected ok={should_ok}, got {result.to_dict()}")
            raise SystemExit(1)
        status = "ok" if result.ok else result.error.code if result.error else "?"
        print(f"[PASS] {label}: {status}")

    html = _html_to_text(
        "<html><head><style>hidden</style></head><body><h1>Hi</h1><p>World</p></body></html>"
    )
    assert "Hi" in html and "World" in html and "hidden" not in html
    print(f"[PASS] html parser: {html!r}")

    try:
        for test_url in ("https://www.python.org/", "https://example.com"):
            live = run({"url": test_url, "max_chars": 2000})
            if live.ok:
                assert live.data["content"]
                assert live.data["final_url"].startswith("https://")
                print(f"[PASS] live {test_url}: {len(live.data['content'])} chars")
                print(to_json(live, indent=2)[:500] + "...")
                break
            if live.error and live.error.code == ToolErrorCode.BLOCKED_HOST:
                print(f"[SKIP] live {test_url}: {live.error.message} (hosts/DNS SSRF guard)")
                continue
            print(f"[SKIP] live {test_url}: {live.error.code if live.error else '?'} — {live.error.message if live.error else ''}")
            break
    except httpx.HTTPError as exc:
        print(f"[SKIP] live fetch: network unavailable — {exc}")


if __name__ == "__main__":
    _demo()
