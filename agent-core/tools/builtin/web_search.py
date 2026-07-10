"""Builtin web_search (TOOLS.md §7.4, TASKS T-104b)."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

_AGENT_CORE = Path(__file__).resolve().parents[2]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tools.http_client import make_httpx_client
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok, to_json

TOOL_NAME = "web_search"
DEFAULT_MAX_RESULTS = 5
HARD_MAX_RESULTS = 10
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True, slots=True)
class WebSearchConfig:
    provider: str
    llm_api_key: str | None
    brave_api_key: str | None
    model: str
    anthropic_base_url: str
    timeout_sec: float


def run(arguments: dict[str, Any], *, paths: Any | None = None) -> ToolResult:
    """Search the web via DeepSeek (default) or Brave."""
    _ = paths
    started = time.perf_counter()

    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return _fail("query is required", ToolErrorCode.VALIDATION_ERROR, started)

    max_results = arguments.get("max_results", DEFAULT_MAX_RESULTS)
    if not isinstance(max_results, int) or max_results < 1:
        return _fail("max_results must be a positive integer", ToolErrorCode.VALIDATION_ERROR, started)
    max_results = min(max_results, HARD_MAX_RESULTS)

    config = _load_config()
    try:
        if config.provider == "brave":
            results = _search_brave(query.strip(), max_results=max_results, config=config)
            provider = "brave"
        else:
            results = _search_deepseek(query.strip(), max_results=max_results, config=config)
            provider = "deepseek"
    except _MissingApiKeyError as exc:
        return _fail(str(exc), ToolErrorCode.MISSING_API_KEY, started)
    except httpx.TimeoutException:
        return _fail("web search timed out", ToolErrorCode.TIMEOUT, started)
    except httpx.HTTPError as exc:
        return _fail(str(exc), ToolErrorCode.NETWORK_ERROR, started)
    except _WebSearchApiError as exc:
        return _fail(str(exc), ToolErrorCode.HTTP_ERROR, started, details=exc.details)
    except _WebSearchError as exc:
        return _fail(str(exc), ToolErrorCode.VALIDATION_ERROR, started)

    return tool_ok(
        TOOL_NAME,
        {"results": results[:max_results], "provider": provider},
        duration_ms=_elapsed_ms(started),
    )


class _WebSearchError(ValueError):
    pass


class _MissingApiKeyError(_WebSearchError):
    pass


class _WebSearchApiError(_WebSearchError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details


def _load_config() -> WebSearchConfig:
    provider = os.environ.get("WEB_SEARCH_PROVIDER", "deepseek").strip().lower()
    if provider not in {"deepseek", "brave"}:
        raise _WebSearchError(f"unsupported WEB_SEARCH_PROVIDER: {provider}")

    timeout_raw = os.environ.get("WEB_SEARCH_TIMEOUT_SEC", "15")
    try:
        timeout_sec = float(timeout_raw)
    except ValueError as exc:
        raise _WebSearchError("WEB_SEARCH_TIMEOUT_SEC must be a number") from exc

    return WebSearchConfig(
        provider=provider,
        llm_api_key=os.environ.get("LLM_API_KEY"),
        brave_api_key=os.environ.get("BRAVE_SEARCH_API_KEY"),
        model=os.environ.get("WEB_SEARCH_MODEL", "deepseek-v4-flash"),
        anthropic_base_url=os.environ.get(
            "WEB_SEARCH_ANTHROPIC_BASE_URL",
            "https://api.deepseek.com/anthropic",
        ).rstrip("/"),
        timeout_sec=timeout_sec,
    )


def _search_deepseek(query: str, *, max_results: int, config: WebSearchConfig) -> list[dict[str, str]]:
    if not config.llm_api_key:
        raise _MissingApiKeyError("LLM_API_KEY is not set (deepseek provider)")

    url = urljoin(f"{config.anthropic_base_url}/", "v1/messages")
    headers = {
        "x-api-key": config.llm_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict[str, Any] = {
        "model": config.model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": f"Search the web for: {query}",
            }
        ],
        "tools": [
            {
                "type": WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": max_results,
            }
        ],
        "tool_choice": {"type": "tool", "name": "web_search"},
    }

    with make_httpx_client(timeout=config.timeout_sec) as client:
        response = _post_with_tool_choice_fallback(client, url, headers=headers, body=body)
        if response.status_code >= 400:
            raise _WebSearchApiError(
                _extract_http_error(response),
                details={"status_code": response.status_code, "provider": "deepseek"},
            )
        payload = response.json()

    return _parse_anthropic_web_search(payload, max_results=max_results)


def _post_with_tool_choice_fallback(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
) -> httpx.Response:
    response = client.post(url, headers=headers, json=body)
    if response.status_code == 400 and "tool_choice" in response.text.lower():
        retry_body = dict(body)
        retry_body["tool_choice"] = {"type": "any"}
        response = client.post(url, headers=headers, json=retry_body)
    return response


def _search_brave(query: str, *, max_results: int, config: WebSearchConfig) -> list[dict[str, str]]:
    if not config.brave_api_key:
        raise _MissingApiKeyError("BRAVE_SEARCH_API_KEY is not set (brave provider)")

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": config.brave_api_key,
    }
    params = {"q": query, "count": str(max_results)}

    with make_httpx_client(timeout=config.timeout_sec) as client:
        response = client.get(BRAVE_SEARCH_URL, headers=headers, params=params)
        if response.status_code >= 400:
            raise _WebSearchApiError(
                _extract_http_error(response),
                details={"status_code": response.status_code, "provider": "brave"},
            )
        payload = response.json()

    web = payload.get("web", {})
    raw_results = web.get("results", []) if isinstance(web, dict) else []
    results: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        results.append(
            {
                "title": str(item.get("title", "")).strip(),
                "url": url,
                "snippet": str(item.get("description", "")).strip(),
            }
        )
    return results[:max_results]


def _parse_anthropic_web_search(payload: dict[str, Any], *, max_results: int) -> list[dict[str, str]]:
    ordered_urls: list[str] = []
    by_url: dict[str, dict[str, str]] = {}

    for block in payload.get("content", []):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "web_search_tool_result":
            content = block.get("content")
            if isinstance(content, dict) and content.get("type") == "web_search_tool_result_error":
                message = content.get("error_message") or content.get("message") or "web search failed"
                raise _WebSearchApiError(str(message), details={"provider": "deepseek"})
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "web_search_result":
                        continue
                    url = str(item.get("url", "")).strip()
                    if not url:
                        continue
                    if url not in by_url:
                        ordered_urls.append(url)
                    by_url[url] = {
                        "title": str(item.get("title", "")).strip(),
                        "url": url,
                        "snippet": "",
                    }
        elif block_type == "text":
            for citation in block.get("citations") or []:
                if not isinstance(citation, dict):
                    continue
                if citation.get("type") != "web_search_result_location":
                    continue
                url = str(citation.get("url", "")).strip()
                if not url:
                    continue
                snippet = str(citation.get("cited_text", "")).strip()
                title = str(citation.get("title", "")).strip()
                if url not in by_url:
                    ordered_urls.append(url)
                    by_url[url] = {"title": title, "url": url, "snippet": snippet}
                elif snippet:
                    by_url[url]["snippet"] = snippet
                    if title and not by_url[url]["title"]:
                        by_url[url]["title"] = title

    return [by_url[url] for url in ordered_urls[:max_results]]


def _extract_http_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return response.text.strip() or f"HTTP {response.status_code}"


def _fail(
    message: str,
    code: str,
    started: float,
    *,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    return tool_fail(
        TOOL_NAME,
        code,
        message,
        duration_ms=_elapsed_ms(started),
        details=details,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _demo() -> None:
    cases: list[tuple[dict[str, Any], bool, str]] = [
        ({}, False, "missing query"),
        ({"query": "   "}, False, "blank query"),
        ({"query": "test", "max_results": 0}, False, "bad max_results"),
        ({"query": "test", "max_results": 99}, False, "needs key even when capped"),
    ]

    previous_provider = os.environ.get("WEB_SEARCH_PROVIDER")
    previous_llm = os.environ.get("LLM_API_KEY")
    previous_brave = os.environ.get("BRAVE_SEARCH_API_KEY")
    os.environ.pop("LLM_API_KEY", None)
    os.environ.pop("BRAVE_SEARCH_API_KEY", None)
    os.environ["WEB_SEARCH_PROVIDER"] = "deepseek"

    try:
        for arguments, should_ok, label in cases:
            result = run(arguments)
            if result.ok != should_ok:
                print(f"[FAIL] {label}: expected ok={should_ok}, got {result.to_dict()}")
                raise SystemExit(1)
            status = "ok" if result.ok else result.error.code if result.error else "?"
            print(f"[PASS] {label}: {status}")

        missing = run({"query": "python"})
        assert not missing.ok and missing.error and missing.error.code == ToolErrorCode.MISSING_API_KEY
        print("[PASS] deepseek missing key")

        os.environ["WEB_SEARCH_PROVIDER"] = "brave"
        missing_brave = run({"query": "python"})
        assert (
            not missing_brave.ok
            and missing_brave.error
            and missing_brave.error.code == ToolErrorCode.MISSING_API_KEY
        )
        print("[PASS] brave missing key")

        capped = run({"query": "python", "max_results": 99})
        assert capped.error and capped.error.code == ToolErrorCode.MISSING_API_KEY
        print("[PASS] validation path before network")

        parsed = _parse_anthropic_web_search(
            {
                "content": [
                    {
                        "type": "web_search_tool_result",
                        "tool_use_id": "srv1",
                        "content": [
                            {
                                "type": "web_search_result",
                                "url": "https://example.com",
                                "title": "Example",
                                "encrypted_content": "opaque",
                            }
                        ],
                    },
                    {
                        "type": "text",
                        "text": "answer",
                        "citations": [
                            {
                                "type": "web_search_result_location",
                                "url": "https://example.com",
                                "title": "Example",
                                "cited_text": "snippet text",
                                "encrypted_index": "opaque",
                            }
                        ],
                    },
                ]
            },
            max_results=5,
        )
        assert parsed == [
            {"title": "Example", "url": "https://example.com", "snippet": "snippet text"}
        ]
        print("[PASS] anthropic response parser")

        if previous_llm:
            os.environ["LLM_API_KEY"] = previous_llm
            live = run({"query": "Python programming language", "max_results": 3})
            if live.ok:
                print(f"[PASS] live deepseek: {len(live.data['results'])} results")
                print(to_json(live, indent=2)[:500] + "...")
            else:
                print(f"[SKIP] live deepseek: {live.error.code} — {live.error.message}")
        else:
            print("[SKIP] live deepseek: LLM_API_KEY not set")
    finally:
        if previous_provider is None:
            os.environ.pop("WEB_SEARCH_PROVIDER", None)
        else:
            os.environ["WEB_SEARCH_PROVIDER"] = previous_provider
        if previous_llm is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = previous_llm
        if previous_brave is None:
            os.environ.pop("BRAVE_SEARCH_API_KEY", None)
        else:
            os.environ["BRAVE_SEARCH_API_KEY"] = previous_brave


if __name__ == "__main__":
    _demo()
