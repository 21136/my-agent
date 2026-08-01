"""OpenAI-compatible LLM thin wrapper for DeepSeek (RUNTIME.md §6, TASKS T-201)."""

from __future__ import annotations

import json
import os
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

from tools.http_client import make_httpx_client

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MODEL_CODING = "deepseek-v4-pro"
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
    )


def resolve_session_model(topics: list[str], *, config: LLMConfig | None = None) -> str:
    """Pick session model: coding topic → pro, else flash (§6.1)."""
    cfg = config or load_config()
    if "coding" in topics:
        return cfg.model_coding
    return cfg.model


def normalize_session_model(raw: str, *, config: LLMConfig | None = None) -> str | None:
    """Map UI/alias strings to configured flash or pro ids; None if unknown."""
    cfg = config or load_config()
    key = raw.strip().casefold().replace("_", "-")
    if not key:
        return None
    flash_keys = {
        cfg.model.casefold(),
        "flash",
        "v4-flash",
        "deepseek-v4-flash",
        DEFAULT_MODEL.casefold(),
    }
    pro_keys = {
        cfg.model_coding.casefold(),
        "pro",
        "v4-pro",
        "deepseek-v4-pro",
        DEFAULT_MODEL_CODING.casefold(),
    }
    if key in flash_keys:
        return cfg.model
    if key in pro_keys:
        return cfg.model_coding
    return None


def llm_model_label(model: str, *, config: LLMConfig | None = None) -> str:
    """Short UI label for session banner / chrome."""
    cfg = config or load_config()
    normalized = normalize_session_model(model, config=cfg)
    if normalized == cfg.model_coding:
        return "Pro"
    if normalized == cfg.model:
        return "Flash"
    if "pro" in model.casefold():
        return "Pro"
    return "Flash"


def resolve_context_limit(model: str, *, config: LLMConfig | None = None) -> int:
    """Context token ceiling for *model* (§6.1 flash 128k / pro 1M)."""
    cfg = config or load_config()
    if cfg.context_limit_override is not None:
        return cfg.context_limit_override
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
        response_format: dict[str, Any] | None = None,
        stream: StreamHandlers | None = None,
    ) -> LLMResponse:
        """POST ``/v1/chat/completions``; streams deltas when ``stream`` handlers are set."""
        self._raise_if_cancelled()
        if not self.config.api_key:
            raise LLMMissingApiKeyError("LLM_API_KEY is not set")

        resolved_model = model or self.config.model
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream is not None,
        }
        if tools:
            payload["tools"] = tools
        if response_format is not None:
            payload["response_format"] = response_format

        url = f"{self.config.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with make_httpx_client(timeout=self.config.timeout_sec) as client:
                with self._active_response_lock:
                    self._active_client = client
                try:
                    if stream is None:
                        response = client.post(url, headers=headers, json=payload)
                        self._raise_if_cancelled()
                        return self._parse_http_response(response, fallback_model=resolved_model)
                    return self._chat_stream(client, url, headers, payload, stream, resolved_model)
                finally:
                    with self._active_response_lock:
                        if self._active_client is client:
                            self._active_client = None
        except httpx.TimeoutException as exc:
            self._raise_if_cancelled()
            raise LLMTimeoutError(
                f"LLM request timed out after {self.config.timeout_sec}s"
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

        data = response.json()
        if not isinstance(data, dict):
            raise LLMApiError("invalid JSON response from provider", status_code=200)

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

    reasoning_content = message.get("reasoning_content")
    if reasoning_content is not None and not isinstance(reasoning_content, str):
        reasoning_content = str(reasoning_content)

    usage = data.get("usage")
    if usage is not None and not isinstance(usage, dict):
        usage = None

    finish_reason = first.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        finish_reason = str(finish_reason)

    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        model = fallback_model

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
            continue

        reasoning_delta = delta.get("reasoning_content")
        if isinstance(reasoning_delta, str) and reasoning_delta:
            reasoning_parts.append(reasoning_delta)
            if handlers.on_reasoning_delta is not None:
                handlers.on_reasoning_delta(reasoning_delta)

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
