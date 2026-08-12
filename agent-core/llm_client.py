"""OpenAI-compatible LLM thin wrapper for DeepSeek (RUNTIME.md §6, TASKS T-201)."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_models import (
    DEFAULT_FLASH_ID,
    DEFAULT_PRO_ID,
    ModelEntry,
    get_registry,
)
from tools.http_client import make_httpx_client

_REASONING_EFFORT_LEVELS = frozenset({"low", "medium", "high", "max"})
_DEEPSEEK_REASONING_EFFORT_LEVELS = frozenset({"low", "high", "max"})


def _normalize_reasoning_effort(raw: str | None) -> str:
    if raw is not None and raw.strip().casefold() in _REASONING_EFFORT_LEVELS:
        return raw.strip().lower()
    return "medium"


def _api_reasoning_effort(effort: str, vendor: str) -> str:
    """Map session effort to provider-supported API values."""
    vendor_key = vendor.casefold()
    if vendor_key in {"deepseek", "sophnet"}:
        if effort == "medium":
            return "high"
        if effort in _DEEPSEEK_REASONING_EFFORT_LEVELS:
            return effort
        return "high"
    return effort


def _apply_reasoning_effort_to_payload(
    payload: dict[str, Any],
    effort: str,
    vendor: str,
) -> None:
    """Attach provider-specific reasoning controls to a chat completion payload."""
    vendor_key = vendor.casefold()
    resolved = _api_reasoning_effort(effort, vendor)
    if vendor_key in {"deepseek", "sophnet"}:
        payload["thinking"] = {
            "type": "enabled",
            "reasoning_effort": resolved,
        }
        return
    if vendor_key == "0x567":
        # OpenAI-compatible gateway: top-level reasoning_effort, not DeepSeek ``thinking``.
        payload["reasoning_effort"] = resolved

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = DEFAULT_FLASH_ID
DEFAULT_MODEL_CODING = DEFAULT_PRO_ID
DEFAULT_TIMEOUT_SEC = 120.0
FLASH_CONTEXT_LIMIT = 128_000
PRO_CONTEXT_LIMIT = 1_000_000


class LLMError(Exception):
    """Base class for LLM client errors."""


class LLMMissingApiKeyError(LLMError):
    """``LLM_API_KEY`` is not set."""


class LLMTimeoutError(LLMError):
    """Request exceeded ``LLM_TIMEOUT_SEC``."""


class LLMCancelledError(LLMError):
    """Request was cancelled by the user."""


class LLMNetworkError(LLMError):
    """Transport-level HTTP failure."""


class LLMApiError(LLMError):
    """Provider returned an HTTP error response."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class LLMConfig:
    api_key: str | None
    base_url: str
    model: str
    model_coding: str
    timeout_sec: float
    context_limit_override: int | None
    reasoning_effort: str = "medium"


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Chat completion result (assembled from stream or single response)."""

    model: str
    content: str | None
    tool_calls: list[dict[str, Any]]
    finish_reason: str | None
    usage: dict[str, Any] | None
    raw: dict[str, Any]
    reasoning_content: str | None = None


@dataclass
class StreamHandlers:
    """Optional callbacks while streaming a chat completion (DESKTOP §5.3)."""

    on_content_delta: Callable[[str], None] | None = None
    on_reasoning_delta: Callable[[str], None] | None = None


def _extract_reasoning_text(payload: dict[str, Any]) -> str | None:
    """Normalize provider-specific reasoning fields to plain text."""
    for key in ("reasoning_content", "reasoning", "thinking"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw:
            return raw
        if isinstance(raw, dict):
            nested = raw.get("content") or raw.get("text")
            if isinstance(nested, str) and nested:
                return nested
    return None


def _append_reasoning_stream(
    parts: list[str],
    handlers: StreamHandlers,
    text: str,
) -> None:
    if not text:
        return
    parts.append(text)
    if handlers.on_reasoning_delta is not None:
        handlers.on_reasoning_delta(text)


def _append_reasoning_full_if_new(
    parts: list[str],
    handlers: StreamHandlers,
    full_text: str,
) -> None:
    """Some providers attach the full reasoning trace on ``message`` not ``delta``."""
    if not full_text:
        return
    joined = "".join(parts)
    if full_text.startswith(joined):
        tail = full_text[len(joined) :]
        if tail:
            _append_reasoning_stream(parts, handlers, tail)
        return
    if full_text not in joined:
        _append_reasoning_stream(parts, handlers, full_text)


def load_config() -> LLMConfig:
    """Load provider settings from environment (RUNTIME.md §6.1)."""
    raw_limit = os.environ.get("LLM_CONTEXT_LIMIT")
    context_limit_override: int | None = None
    if raw_limit is not None and raw_limit.strip():
        context_limit_override = int(raw_limit.strip())

    raw_timeout = os.environ.get("LLM_TIMEOUT_SEC", str(int(DEFAULT_TIMEOUT_SEC)))
    return LLMConfig(
        api_key=os.environ.get("LLM_API_KEY"),
        base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        model_coding=os.environ.get("LLM_MODEL_CODING", DEFAULT_MODEL_CODING),
        timeout_sec=float(raw_timeout),
        context_limit_override=context_limit_override,
        reasoning_effort=_normalize_reasoning_effort(
            os.environ.get("LLM_REASONING_EFFORT")
        ),
    )


def resolve_session_model(topics: list[str], *, config: LLMConfig | None = None) -> str:
    """Pick session model: coding topic → pro tier default, else flash (§6.1)."""
    registry = get_registry()
    if "coding" in topics:
        return registry.default_pro_id
    return registry.default_flash_id


def normalize_session_model(raw: str, *, config: LLMConfig | None = None) -> str | None:
    """Map UI/alias strings to registry model ids; None if unknown."""
    entry = get_registry().resolve(raw)
    if entry is None:
        return None
    return entry.id


def resolve_model_entry(model: str) -> ModelEntry | None:
    """Resolve a session / chat model id to a registry entry."""
    return get_registry().resolve(model)


def vendor_supports_system_prompt_cache(vendor: str) -> bool:
    """Return whether a provider accepts explicit system prompt cache markers."""
    return False


def as_cached_system_message(prompt: str) -> dict[str, Any]:
    """Build an OpenAI-compatible system message."""
    return {"role": "system", "content": prompt}


def build_chat_messages(
    messages: list[dict[str, Any]],
    *,
    system_prompt: str,
    static_system: str,
    dynamic_system: str,
    vendor: str,
) -> list[dict[str, Any]]:
    """Assemble provider-compatible chat messages with optional cache splitting."""
    if not vendor_supports_system_prompt_cache(vendor):
        return ([{"role": "system", "content": system_prompt}] if system_prompt else []) + list(messages)

    assembled: list[dict[str, Any]] = []
    if static_system:
        assembled.append(as_cached_system_message(static_system))
    if dynamic_system:
        assembled.append({"role": "system", "content": dynamic_system})
    if not assembled and system_prompt:
        assembled.append(as_cached_system_message(system_prompt))
    assembled.extend(messages)
    return assembled


def cached_prompt_tokens(usage: dict[str, Any] | None) -> int:
    """Extract provider cache-read token usage from a completion usage object."""
    if not isinstance(usage, dict):
        return 0
    direct = usage.get("cache_read_input_tokens")
    if isinstance(direct, (int, float)):
        return int(direct)
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens")
        if isinstance(cached, (int, float)):
            return int(cached)
    return 0


def llm_usage_event(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize provider token usage for the terminal and desktop event streams."""
    if not isinstance(usage, dict):
        return None
    prompt_tokens = usage.get("prompt_tokens")
    if not isinstance(prompt_tokens, (int, float)):
        return None
    completion_tokens = usage.get("completion_tokens")
    event: dict[str, Any] = {
        "type": "llm.usage",
        "prompt_tokens": int(prompt_tokens),
        "cached_tokens": cached_prompt_tokens(usage),
    }
    if isinstance(completion_tokens, (int, float)):
        event["completion_tokens"] = int(completion_tokens)
    event["cache_ratio"] = (
        event["cached_tokens"] / event["prompt_tokens"]
        if event["prompt_tokens"]
        else 0.0
    )
    return event


def llm_model_label(model: str, *, config: LLMConfig | None = None) -> str:
    """Short UI label for session banner / chrome."""
    entry = get_registry().resolve(model)
    if entry is not None:
        return entry.name
    if "pro" in model.casefold():
        return "Pro"
    return "Flash"


def resolve_context_limit(model: str, *, config: LLMConfig | None = None) -> int:
    """Context token ceiling for *model* (registry max_input_tokens)."""
    cfg = config or load_config()
    if cfg.context_limit_override is not None:
        return cfg.context_limit_override
    entry = get_registry().resolve(model)
    if entry is not None:
        return entry.max_input_tokens
    if model == cfg.model_coding:
        return PRO_CONTEXT_LIMIT
    return FLASH_CONTEXT_LIMIT


class LLMClient:
    """Thin OpenAI-compatible client; streaming when ``stream`` handlers are passed."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or load_config()
        self._cancel_event: threading.Event | None = None
        self._active_client: httpx.Client | None = None
        self._active_response: httpx.Response | None = None
        self._active_response_lock = threading.Lock()

    def set_cancel_event(self, event: threading.Event) -> None:
        self._cancel_event = event

    def cancel_current_request(self) -> None:
        """Interrupt an active streaming response from another thread."""
        with self._active_response_lock:
            response = self._active_response
            client = self._active_client
        if response is not None:
            try:
                response.close()
            except (httpx.HTTPError, RuntimeError):
                pass
        if client is not None:
            try:
                client.close()
            except (httpx.HTTPError, RuntimeError):
                pass

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise LLMCancelledError("LLM request cancelled")

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        reasoning_effort: str | None = None,
        response_format: dict[str, Any] | None = None,
        stream: StreamHandlers | None = None,
        timeout_sec: float | None = None,
    ) -> LLMResponse:
        """POST ``/v1/chat/completions``; streams deltas when ``stream`` handlers are set."""
        self._raise_if_cancelled()
        effective_timeout = self.config.timeout_sec if timeout_sec is None else timeout_sec
        registry = get_registry()
        model_ref = model or registry.default_flash_id
        entry = registry.resolve(model_ref)
        if entry is None:
            raise LLMApiError(f"unsupported llm model: {model_ref!r}", status_code=400)

        api_key = entry.resolve_api_key()
        if not api_key:
            env_hint = entry.api_key_env or "LLM_API_KEY"
            raise LLMMissingApiKeyError(f"API key not set for model {entry.id} (set {env_hint})")

        resolved_model = entry.provider_model
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream is not None,
        }
        if tools and entry.supports_tool_call:
            payload["tools"] = tools
        if response_format is not None:
            payload["response_format"] = response_format
        if reasoning_effort is not None:
            _apply_reasoning_effort_to_payload(
                payload,
                reasoning_effort,
                entry.vendor,
            )

        url = entry.chat_completions_url()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            with make_httpx_client(timeout=effective_timeout) as client:
                with self._active_response_lock:
                    self._active_client = client
                try:
                    if stream is None:
                        response = client.post(url, headers=headers, json=payload)
                        self._raise_if_cancelled()
                        return self._parse_http_response(
                            response,
                            fallback_model=entry.id,
                        )
                    return self._chat_stream(client, url, headers, payload, stream, entry.id)
                finally:
                    with self._active_response_lock:
                        if self._active_client is client:
                            self._active_client = None
        except httpx.TimeoutException as exc:
            self._raise_if_cancelled()
            raise LLMTimeoutError(
                f"LLM request timed out after {effective_timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            self._raise_if_cancelled()
            raise LLMNetworkError(str(exc)) from exc

    def _parse_http_response(
        self,
        response: httpx.Response,
        *,
        fallback_model: str,
    ) -> LLMResponse:
        if response.status_code >= 400:
            raise LLMApiError(
                _extract_http_error(response),
                status_code=response.status_code,
            )

        data = _load_response_json(response)
        return _parse_completion(data, fallback_model=fallback_model)

    def _chat_stream(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        handlers: StreamHandlers,
        fallback_model: str,
    ) -> LLMResponse:
        with client.stream("POST", url, headers=headers, json=payload) as response:
            with self._active_response_lock:
                self._active_response = response
            try:
                self._raise_if_cancelled()
                if response.status_code >= 400:
                    raise LLMApiError(
                        _extract_http_error(response),
                        status_code=response.status_code,
                    )
                return _consume_sse_stream(
                    response,
                    handlers=handlers,
                    fallback_model=fallback_model,
                    cancel_event=self._cancel_event,
                )
            finally:
                with self._active_response_lock:
                    if self._active_response is response:
                        self._active_response = None


_PROVIDER_JSON_ERROR_RE = re.compile(
    r"^Expecting (?:value|property name enclosed in double quotes):",
    re.IGNORECASE,
)


def _load_response_json(response: httpx.Response) -> dict[str, Any]:
    """Parse HTTP body as JSON; raise LLMApiError on empty or invalid payloads."""
    text = _response_text(response).strip()
    if not text:
        raise LLMApiError(
            "provider returned empty response body",
            status_code=response.status_code,
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:240].replace("\n", " ")
        raise LLMApiError(
            f"provider returned non-JSON body (HTTP {response.status_code}): {preview}",
            status_code=response.status_code,
        ) from exc
    if not isinstance(data, dict):
        raise LLMApiError(
            "invalid JSON response from provider",
            status_code=response.status_code,
        )
    return data


def _raise_if_provider_error_content(content: str | None) -> None:
    """Reject completions that echo provider-side JSON parse failures."""
    if not content:
        return
    stripped = content.strip()
    if not stripped:
        return
    if _PROVIDER_JSON_ERROR_RE.match(stripped):
        raise LLMApiError(
            f"provider returned error text instead of a completion: {stripped[:200]}",
            status_code=200,
        )


def _parse_completion(data: dict[str, Any], *, fallback_model: str) -> LLMResponse:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMApiError("response missing choices", status_code=200)

    first = choices[0]
    if not isinstance(first, dict):
        raise LLMApiError("invalid choice payload", status_code=200)

    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMApiError("response missing message", status_code=200)

    raw_tool_calls = message.get("tool_calls")
    tool_calls: list[dict[str, Any]] = []
    if isinstance(raw_tool_calls, list):
        tool_calls = [item for item in raw_tool_calls if isinstance(item, dict)]

    content = message.get("content")
    if content is not None and not isinstance(content, str):
        content = str(content)

    reasoning_content = _extract_reasoning_text(message)

    usage = data.get("usage")
    if usage is not None and not isinstance(usage, dict):
        usage = None

    finish_reason = first.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        finish_reason = str(finish_reason)

    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        model = fallback_model

    _raise_if_provider_error_content(content)

    return LLMResponse(
        model=model,
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        raw=data,
        reasoning_content=reasoning_content,
    )


def _consume_sse_stream(
    response: httpx.Response,
    *,
    handlers: StreamHandlers,
    fallback_model: str,
    cancel_event: threading.Event | None = None,
) -> LLMResponse:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    model = fallback_model
    usage: dict[str, Any] | None = None
    last_raw: dict[str, Any] = {}

    for line in response.iter_lines():
        if cancel_event is not None and cancel_event.is_set():
            raise LLMCancelledError("LLM request cancelled")
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        last_raw = chunk
        chunk_model = chunk.get("model")
        if isinstance(chunk_model, str) and chunk_model.strip():
            model = chunk_model.strip()
        chunk_usage = chunk.get("usage")
        if isinstance(chunk_usage, dict):
            usage = chunk_usage

        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            finish_reason = reason

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}

        reasoning_delta = _extract_reasoning_text(delta)
        if reasoning_delta:
            _append_reasoning_stream(reasoning_parts, handlers, reasoning_delta)

        message = choice.get("message")
        if isinstance(message, dict):
            reasoning_message = _extract_reasoning_text(message)
            if reasoning_message:
                _append_reasoning_full_if_new(reasoning_parts, handlers, reasoning_message)

        content_delta = delta.get("content")
        if isinstance(content_delta, str) and content_delta:
            content_parts.append(content_delta)
            if handlers.on_content_delta is not None:
                handlers.on_content_delta(content_delta)

        raw_tool_calls = delta.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            _merge_stream_tool_calls(tool_calls, raw_tool_calls)

    if cancel_event is not None and cancel_event.is_set():
        raise LLMCancelledError("LLM request cancelled")

    assembled_tool_calls = [tool_calls[idx] for idx in sorted(tool_calls)]
    content = "".join(content_parts) or None
    reasoning_content = "".join(reasoning_parts) or None
    _raise_if_provider_error_content(content)
    return LLMResponse(
        model=model,
        content=content,
        tool_calls=assembled_tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        raw=last_raw,
        reasoning_content=reasoning_content,
    )


def _merge_stream_tool_calls(
    accumulator: dict[int, dict[str, Any]],
    deltas: list[Any],
) -> None:
    for item in deltas:
        if not isinstance(item, dict):
            continue
        index_raw = item.get("index", 0)
        try:
            index = int(index_raw)
        except (TypeError, ValueError):
            index = 0
        current = accumulator.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if isinstance(item.get("id"), str) and item["id"]:
            current["id"] = item["id"]
        if isinstance(item.get("type"), str) and item["type"]:
            current["type"] = item["type"]
        fn = item.get("function")
        if isinstance(fn, dict):
            target_fn = current.setdefault("function", {"name": "", "arguments": ""})
            if isinstance(fn.get("name"), str) and fn["name"]:
                target_fn["name"] = fn["name"]
            if isinstance(fn.get("arguments"), str) and fn["arguments"]:
                target_fn["arguments"] = str(target_fn.get("arguments", "")) + fn["arguments"]


def _response_text(response: httpx.Response) -> str:
    """Read HTTP body as text for both normal and streaming responses."""
    try:
        raw = response.read()
        if raw:
            return raw.decode("utf-8", errors="replace")
    except Exception:
        pass
    try:
        return response.text
    except Exception:
        return ""


def _extract_http_error(response: httpx.Response) -> str:
    text = _response_text(response).strip()
    if not text:
        return f"HTTP {response.status_code}"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return text or f"HTTP {response.status_code}"


def _demo() -> None:
    cfg = load_config()
    print(f"base_url: {cfg.base_url}")
    print(f"model (flash): {cfg.model}")
    print(f"model_coding (pro): {cfg.model_coding}")
    print(f"timeout_sec: {cfg.timeout_sec}")
    print()

    # Defaults
    assert cfg.base_url == DEFAULT_BASE_URL, cfg.base_url
    assert cfg.model == DEFAULT_MODEL, cfg.model
    assert cfg.model_coding == DEFAULT_MODEL_CODING, cfg.model_coding
    assert cfg.timeout_sec == DEFAULT_TIMEOUT_SEC, cfg.timeout_sec
    print("[PASS] default config from env")

    # Session model resolution (§6.1)
    assert resolve_session_model([], config=cfg) == DEFAULT_MODEL
    assert resolve_session_model(["workflow"], config=cfg) == DEFAULT_MODEL
    assert resolve_session_model(["coding"], config=cfg) == DEFAULT_MODEL_CODING
    assert resolve_session_model(["workflow", "coding"], config=cfg) == DEFAULT_MODEL_CODING
    print("[PASS] resolve_session_model: flash vs coding pro")

    # Context limits (§6.1)
    assert resolve_context_limit(cfg.model, config=cfg) == FLASH_CONTEXT_LIMIT
    assert resolve_context_limit(cfg.model_coding, config=cfg) == PRO_CONTEXT_LIMIT
    print("[PASS] resolve_context_limit: flash 128k / pro 1M")

    prev_limit = os.environ.get("LLM_CONTEXT_LIMIT")
    os.environ["LLM_CONTEXT_LIMIT"] = "99999"
    try:
        overridden = load_config()
        assert resolve_context_limit(cfg.model, config=overridden) == 99_999
        assert resolve_context_limit(cfg.model_coding, config=overridden) == 99_999
        print("[PASS] LLM_CONTEXT_LIMIT env override")
    finally:
        if prev_limit is None:
            os.environ.pop("LLM_CONTEXT_LIMIT", None)
        else:
            os.environ["LLM_CONTEXT_LIMIT"] = prev_limit

    # Missing API key
    no_key = LLMConfig(
        api_key=None,
        base_url=cfg.base_url,
        model=cfg.model,
        model_coding=cfg.model_coding,
        timeout_sec=cfg.timeout_sec,
        context_limit_override=None,
    )
    client = LLMClient(no_key)
    try:
        client.chat([{"role": "user", "content": "hi"}])
        print("[FAIL] expected LLMMissingApiKeyError")
        raise SystemExit(1)
    except LLMMissingApiKeyError:
        print("[PASS] missing LLM_API_KEY raises LLMMissingApiKeyError")

    # Timeout handling (unreachable host, very short timeout)
    timeout_cfg = LLMConfig(
        api_key="test-key",
        base_url="http://127.0.0.1:1",
        model=cfg.model,
        model_coding=cfg.model_coding,
        timeout_sec=0.5,
        context_limit_override=None,
    )
    timeout_client = LLMClient(timeout_cfg)
    try:
        timeout_client.chat([{"role": "user", "content": "hi"}])
        print("[FAIL] expected LLMTimeoutError or LLMNetworkError")
        raise SystemExit(1)
    except (LLMTimeoutError, LLMNetworkError):
        print("[PASS] timeout / network error on unreachable endpoint")

    # Response parsing
    parsed = _parse_completion(
        {
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "hello",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "grep", "arguments": "{}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
        fallback_model=cfg.model,
    )
    assert parsed.content == "hello"
    assert len(parsed.tool_calls) == 1
    assert parsed.finish_reason == "tool_calls"
    assert parsed.usage is not None and parsed.usage["total_tokens"] == 15
    print("[PASS] _parse_completion extracts content, tool_calls, usage")

    # Empty / invalid HTTP bodies → LLMApiError (not raw JSONDecodeError)
    class _FakeResponse:
        def __init__(self, *, status_code: int, body: str) -> None:
            self.status_code = status_code
            self._body = body.encode("utf-8")

        def read(self) -> bytes:
            return self._body

        @property
        def text(self) -> str:
            return self._body.decode("utf-8")

    client_for_parse = LLMClient(cfg)
    try:
        client_for_parse._parse_http_response(
            _FakeResponse(status_code=200, body=""),
            fallback_model=cfg.model,
        )
        print("[FAIL] expected LLMApiError on empty body")
        raise SystemExit(1)
    except LLMApiError as exc:
        assert "empty response body" in str(exc)
        print("[PASS] empty provider body raises LLMApiError")

    try:
        _parse_completion(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Expecting value: line 1 column 1 (char 0)",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
            fallback_model=cfg.model,
        )
        print("[FAIL] expected LLMApiError on provider error content")
        raise SystemExit(1)
    except LLMApiError as exc:
        assert "error text instead of a completion" in str(exc)
        print("[PASS] provider JSON error text rejected in completion")

    # Streaming SSE assembly (offline)
    class _FakeStreamResponse:
        def __init__(self, lines: list[str]) -> None:
            self._lines = lines

        def iter_lines(self):
            return iter(self._lines)

    deltas: list[str] = []
    reasoning: list[str] = []
    streamed = _consume_sse_stream(
        _FakeStreamResponse(
            [
                'data: {"model":"deepseek-v4-flash","choices":[{"delta":{"reasoning_content":"think "},"index":0}]}',
                'data: {"choices":[{"delta":{"reasoning_content":"more"},"index":0}]}',
                'data: {"choices":[{"delta":{"content":"hel"},"index":0}]}',
                'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop","index":0}]}',
                "data: [DONE]",
            ]
        ),
        handlers=StreamHandlers(
            on_content_delta=deltas.append,
            on_reasoning_delta=reasoning.append,
        ),
        fallback_model=cfg.model,
    )
    assert streamed.content == "hello"
    assert streamed.reasoning_content == "think more"
    assert deltas == ["hel", "lo"]
    assert reasoning == ["think ", "more"]
    print("[PASS] _consume_sse_stream: content + reasoning deltas")

    message_only = _consume_sse_stream(
        _FakeStreamResponse(
            [
                'data: {"choices":[{"message":{"reasoning_content":"full trace"},"index":0}]}',
                "data: [DONE]",
            ]
        ),
        handlers=StreamHandlers(on_reasoning_delta=reasoning.append),
        fallback_model=cfg.model,
    )
    assert message_only.reasoning_content == "full trace"
    print("[PASS] _consume_sse_stream: message.reasoning_content fallback")

    tool_events: list[str] = []
    tool_streamed = _consume_sse_stream(
        _FakeStreamResponse(
            [
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"grep","arguments":"{\\"p"}}]},"index":0}]}',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ath\\":\\"x\\"}"}}]},"index":0}]}',
                'data: {"choices":[{"finish_reason":"tool_calls","index":0}]}',
                "data: [DONE]",
            ]
        ),
        handlers=StreamHandlers(on_content_delta=tool_events.append),
        fallback_model=cfg.model,
    )
    assert len(tool_streamed.tool_calls) == 1
    assert tool_streamed.tool_calls[0]["function"]["name"] == "grep"
    assert tool_streamed.finish_reason == "tool_calls"
    print("[PASS] _consume_sse_stream: tool_calls accumulation")

    stream_err = httpx.Response(
        401,
        request=httpx.Request("POST", "https://api.example.com/v1/chat/completions"),
        stream=httpx.ByteStream(b'{"error":{"message":"invalid api key"}}'),
    )
    assert _extract_http_error(stream_err) == "invalid api key"
    print("[PASS] _extract_http_error: streaming error body")

    # Optional live call
    if cfg.api_key:
        live_client = LLMClient(cfg)
        started = time.perf_counter()
        reply = live_client.chat(
            [{"role": "user", "content": "Reply with exactly: pong"}],
            model=cfg.model,
            temperature=0,
        )
        elapsed = time.perf_counter() - started
        assert reply.content, "empty content from live API"
        print(f"[PASS] live chat ({elapsed:.1f}s): model={reply.model!r}")
        print(f"       content preview: {reply.content[:120]!r}")
    else:
        print("[SKIP] live chat: LLM_API_KEY not set")


if __name__ == "__main__":
    _demo()
