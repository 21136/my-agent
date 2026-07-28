"""httpx client factory for builtin network tools."""

from __future__ import annotations

import os

import httpx


def _trust_proxy_env() -> bool:
    """Opt-in: honor ``HTTP(S)_PROXY`` / ``ALL_PROXY`` (``MY_AGENT_HTTP_TRUST_ENV=1``)."""
    raw = os.environ.get("MY_AGENT_HTTP_TRUST_ENV", "").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def make_httpx_client(*, timeout: float) -> httpx.Client:
    """Return an httpx client for outbound API calls.

    Default **direct** connect (``trust_env=False``) so Clash / system proxy env
    vars do not MITM LLM HTTPS and trigger ``CERTIFICATE_VERIFY_FAILED``.

    Set ``MY_AGENT_HTTP_TRUST_ENV=1`` when the host truly needs a proxy to reach APIs.
    If SOCKS proxy is enabled but ``socksio`` is missing, fall back to direct.
    """
    if not _trust_proxy_env():
        return httpx.Client(timeout=timeout, trust_env=False)
    try:
        return httpx.Client(timeout=timeout)
    except ImportError as exc:
        if "socks" in str(exc).lower():
            return httpx.Client(timeout=timeout, trust_env=False)
        raise
