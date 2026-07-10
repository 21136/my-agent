"""OpenAI-compatible LLM thin wrapper for DeepSeek (RUNTIME.md §6, TASKS T-201)."""

from __future__ import annotations

import os
import sys
import time
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
    """Non-streaming chat completion result."""

    model: str
    content: str | None
    tool_calls: list[dict[str, Any]]
    finish_reason: str | None
    usage: dict[str, Any] | None
    raw: dict[str, Any]


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


def resolve_context_limit(model: str, *, config: LLMConfig | None = None) -> int:
    """Context token ceiling for *model* (§6.1 flash 128k / pro 1M)."""
    cfg = config or load_config()
    if cfg.context_limit_override is not None:
        return cfg.context_limit_override
    if model == cfg.model_coding:
        return PRO_CONTEXT_LIMIT
    return FLASH_CONTEXT_LIMIT


class LLMClient:
    """Thin OpenAI-compatible client; no streaming (§6.3)."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or load_config()

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """POST ``/v1/chat/completions``; returns the full message when done."""
        if not self.config.api_key:
            raise LLMMissingApiKeyError("LLM_API_KEY is not set")

        resolved_model = model or self.config.model
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
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
                response = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"LLM request timed out after {self.config.timeout_sec}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMNetworkError(str(exc)) from exc

        if response.status_code >= 400:
            raise LLMApiError(
                _extract_http_error(response),
                status_code=response.status_code,
            )

        data = response.json()
        if not isinstance(data, dict):
            raise LLMApiError("invalid JSON response from provider", status_code=200)

        return _parse_completion(data, fallback_model=resolved_model)


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
    )


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
