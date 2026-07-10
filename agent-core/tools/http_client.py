"""httpx client factory for builtin network tools."""

from __future__ import annotations

import httpx


def make_httpx_client(*, timeout: float) -> httpx.Client:
    """Return an httpx client, falling back to direct connect when SOCKS proxy breaks.

    On Windows, tools like Clash often set ``ALL_PROXY=socks5://...``. httpx then
    requires ``socksio`` (``pip install httpx[socks]``). If that extra is missing,
    retry with ``trust_env=False`` so API calls still work without crashing.
    """
    try:
        return httpx.Client(timeout=timeout)
    except ImportError as exc:
        if "socks" in str(exc).lower():
            return httpx.Client(timeout=timeout, trust_env=False)
        raise
