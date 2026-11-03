"""OpenAI-compatible LLM thin wrapper for DeepSeek (RUNTIME.md §6, TASKS T-201)."""

from __future__ import annotations

import json
import math
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
    ModelRegistry,
    get_registry,
    is_tokeness_gateway,
)
from paths import AgentPaths
from tools.http_client import make_httpx_client

_REASONING_EFFORT_LEVELS = frozenset({"low", "medium", "high", "max"})
_DEEPSEEK_REASONING_EFFORT_LEVELS = frozenset({"low", "high", "max"})
_GPT6_CHAT_MODELS = frozenset({"gpt-6-luna", "gpt-6-sol"})


def _is_gpt6_provider_model(entry: ModelEntry) -> bool:
    return entry.provider_model.casefold() in _GPT6_CHAT_MODELS


def _gpt6_omit_reasoning_effort_with_tools(entry: ModelEntry, *, has_tools: bool) -> bool:
    """GPT-6 on 0x567 rejects reasoning_effort=none; omit the field when tools are present."""
    return _is_gpt6_provider_model(entry) and has_tools


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
    if vendor_key in {"0x567", "tokeness"}:
        # OpenAI-compatible gateways may reject ``max``; cap it at ``high``.
        if effort == "max":
            return "high"
    return effort


def _apply_reasoning_effort_to_payload(
    payload: dict[str, Any],
    effort: str,
    entry: ModelEntry,
    *,
    has_tools: bool = False,
) -> None:
    """Attach provider-specific reasoning controls to a chat completion payload."""
    vendor_key = entry.vendor.casefold()
    resolved = _api_reasoning_effort(effort, entry.vendor)
    if _gpt6_omit_reasoning_effort_with_tools(entry, has_tools=has_tools):
        return
    if vendor_key in {"deepseek", "sophnet"}:
        payload["thinking"] = {
            "type": "enabled",
            "reasoning_effort": resolved,
        }
        return
    if vendor_key in {"0x567", "tokeness"} or is_tokeness_gateway(entry):
        # Tokeness gpt-5.6-luna on /v1/chat/completions rejects tools + reasoning_effort.
        if is_tokeness_gateway(entry) and has_tools:
            payload["reasoning_effort"] = "none"
            return
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


class LLMConfirmedBatchError(LLMError):
    """Batch stopped; attempted calls may have reached the provider."""
    def __init__(self, *, attempted: int, remaining: int):
        self.attempted = attempted
        self.remaining = remaining
        super().__init__("confirmed batch stopped; attempted requests may have been sent")


class LLMOutboundConfirmationError(LLMError):
    """A confirmed outbound request was not explicitly approved."""


class LLMOutboundConfigurationChangedError(LLMError):
    """The frozen outbound request, registry profile, or credentials could not be matched."""


class LLMOutboundRequestError(LLMError):
    """A single confirmed HTTP request failed and must not be retried automatically."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
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


# Reserved top-level message namespace: untrusted input, never provider content.
HOST_INTERNAL_METADATA_KEY = "_host_internal"


def _provider_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove only per-message host metadata without changing content or tools."""
    return [{key: value for key, value in message.items() if key != HOST_INTERNAL_METADATA_KEY}
            if isinstance(message, dict) else message for message in messages]


def _clone_json_value(value: Any) -> Any:
    """Copy a plain JSON value so caller mutation cannot change a confirmed body."""
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return value
    if type(value) is list:
        return [_clone_json_value(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("JSON object keys must be strings")
        return {key: _clone_json_value(item) for key, item in value.items()}
    raise ValueError("value is not a plain JSON type")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8", errors="strict")


def _confirmed_endpoint(entry: ModelEntry) -> tuple[str, httpx.URL]:
    raw = entry.chat_completions_url()
    if type(raw) is not str or not raw or any(ord(char) < 33 for char in raw):
        raise ValueError("endpoint is invalid")
    url = httpx.URL(raw)
    if (url.scheme != "https" or not url.host or url.username or url.password
            or url.query or url.fragment or str(url) != raw):
        raise ValueError("endpoint is not a canonical HTTPS URL")
    return raw, url


def _confirmed_label(value: Any) -> bool:
    if type(value) is not str or not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


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


def format_context_tokens_short(tokens: int) -> str:
    """Compact UI label for context limits (e.g. 372k, 1M)."""
    if tokens >= 1_000_000:
        millions = tokens / 1_000_000
        if millions == int(millions):
            return f"{int(millions)}M"
        return f"{millions:.1f}M"
    if tokens >= 1_000:
        thousands = tokens / 1_000
        if thousands == int(thousands):
            return f"{int(thousands)}k"
        return f"{thousands:.1f}k"
    return str(tokens)


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

    def __init__(
        self, config: LLMConfig | None = None, *, paths: AgentPaths | None = None
    ) -> None:
        self.config = config or load_config()
        self.paths = paths
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
        registry = (
            get_registry(self.paths) if self.paths is not None else get_registry()
        )
        model_ref = model or registry.default_flash_id
        entry = registry.resolve(model_ref)
        if entry is None:
            raise LLMApiError(f"unsupported llm model: {model_ref!r}", status_code=400)

        api_key = (
            entry.resolve_api_key(self.paths)
            if self.paths is not None
            else entry.resolve_api_key()
        )
        if not api_key:
            env_hint = entry.api_key_env or "LLM_API_KEY"
            raise LLMMissingApiKeyError(f"API key not set for model {entry.id} (set {env_hint})")

        resolved_model = entry.provider_model
        streaming = stream is not None
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": _provider_messages(messages),
            "temperature": temperature,
            "stream": streaming,
        }
        if streaming:
            # Desktop/sidecar always stream; providers omit usage unless asked.
            payload["stream_options"] = {"include_usage": True}
        if tools and entry.supports_tool_call:
            payload["tools"] = tools
        if response_format is not None:
            payload["response_format"] = response_format
        has_tools_in_payload = bool(payload.get("tools"))
        if reasoning_effort is not None:
            _apply_reasoning_effort_to_payload(
                payload,
                reasoning_effort,
                entry,
                has_tools=has_tools_in_payload,
            )
        elif is_tokeness_gateway(entry) and has_tools_in_payload:
            # Belt-and-suspenders: Tokeness requires explicit none when tools are present.
            payload["reasoning_effort"] = "none"

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

    def chat_confirmed_json(
        self,
        messages: list[dict[str, Any]],
        *,
        model_id: str,
        session_id: str,
        expected_preview_json: str,
        confirm_fn: Callable[[str, bool], str],
        before_send_fn: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        """Send one frozen non-streaming request with exact-confirm and an optional final guard.

        ``expected_preview_json`` is the host's already prepared preview. It must
        byte-for-byte match the canonical preview assembled here before the
        confirmation callback runs. The callback is called exactly once as
        ``confirm_fn(preview, False)``; only the exact string ``"y"`` approves.
        The request body is frozen before confirmation, the effective registry
        entry and credential are rechecked afterwards, and the same body bytes
        are sent once without environment proxies, redirects, retry, or fallback.
        """
        self._raise_if_cancelled()
        if (type(model_id) is not str or not model_id or model_id != model_id.strip()
                or not _confirmed_label(model_id) or type(session_id) is not str
                or not _confirmed_label(session_id) or type(messages) is not list
                or type(expected_preview_json) is not str or not callable(confirm_fn)
                or (before_send_fn is not None and not callable(before_send_fn))):
            raise LLMOutboundConfigurationChangedError("confirmed request inputs are invalid")

        try:
            frozen_messages = _clone_json_value(_provider_messages(messages))
            if type(frozen_messages) is not list or any(type(item) is not dict for item in frozen_messages):
                raise ValueError("messages must be a list of JSON objects")
            initial_registry = ModelRegistry.load(self.paths)
            initial_entry = initial_registry.resolve(model_id)
            if (type(initial_entry) is not ModelEntry or initial_entry.id != model_id
                    or not _confirmed_label(initial_entry.id)
                    or not _confirmed_label(initial_entry.vendor)
                    or not _confirmed_label(initial_entry.provider_model)):
                raise ValueError("model profile is unavailable or non-canonical")
            endpoint_text, endpoint_url = _confirmed_endpoint(initial_entry)
            provider_payload = {
                "model": initial_entry.provider_model,
                "messages": frozen_messages,
                "temperature": 0.0,
                "stream": False,
                "response_format": {"type": "json_object"},
            }
            body = _canonical_json_bytes(provider_payload)
            preview = json.dumps({
                "model_id": initial_entry.id,
                "vendor": initial_entry.vendor,
                "endpoint": endpoint_text,
                "session_id": session_id,
                "provider_payload": provider_payload,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if preview != expected_preview_json:
                raise ValueError("prepared preview does not match the frozen request")
            first_api_key = initial_entry.resolve_api_key(self.paths)
        except LLMMissingApiKeyError:
            raise
        except Exception:
            raise LLMOutboundConfigurationChangedError(
                "confirmed request configuration or exact preview is invalid"
            ) from None
        if type(first_api_key) is not str or not first_api_key:
            raise LLMMissingApiKeyError(f"API key not set for model {initial_entry.id}")

        try:
            choice = confirm_fn(preview, False)
        except Exception:
            raise LLMOutboundConfirmationError("confirmed request was not approved") from None
        if type(choice) is not str or choice != "y":
            raise LLMOutboundConfirmationError("confirmed request was not approved")
        self._raise_if_cancelled()

        try:
            current_registry = ModelRegistry.load(self.paths)
            current_entry = current_registry.resolve(model_id)
            if (type(current_entry) is not ModelEntry or current_entry != initial_entry
                    or current_entry.id != model_id):
                raise ValueError("model registry changed")
            current_endpoint, current_url = _confirmed_endpoint(current_entry)
            if current_endpoint != endpoint_text or current_url != endpoint_url:
                raise ValueError("endpoint changed")
            current_payload = {
                "model": current_entry.provider_model,
                "messages": frozen_messages,
                "temperature": 0.0,
                "stream": False,
                "response_format": {"type": "json_object"},
            }
            if _canonical_json_bytes(current_payload) != body:
                raise ValueError("provider payload changed")
            current_api_key = current_entry.resolve_api_key(self.paths)
            if (type(current_api_key) is not str or not current_api_key
                    or current_api_key != first_api_key):
                raise ValueError("credential changed")
        except Exception:
            raise LLMOutboundConfigurationChangedError(
                "confirmed request configuration changed; no request was sent"
            ) from None
        self._raise_if_cancelled()

        headers = {
            "Authorization": f"Bearer {current_api_key}",
            "Content-Type": "application/json",
        }
        client: httpx.Client | None = None
        response: httpx.Response | None = None
        try:
            # This path deliberately bypasses make_httpx_client's opt-in proxy-env behavior.
            client = httpx.Client(
                timeout=self.config.timeout_sec,
                trust_env=False,
                follow_redirects=False,
            )
            with self._active_response_lock:
                self._active_client = client
            with client:
                # This trusted host guard observes authorization changes after client
                # setup, immediately before handing the frozen body to HTTP.
                self._raise_if_cancelled()
                if before_send_fn is not None:
                    try:
                        send_allowed = before_send_fn()
                    except Exception:
                        send_allowed = False
                    if send_allowed is not True:
                        raise LLMOutboundConfigurationChangedError(
                            "confirmed request authorization changed; no request was sent"
                        )
                self._raise_if_cancelled()
                response = client.post(
                    endpoint_text,
                    headers=headers,
                    content=body,
                    follow_redirects=False,
                )
                with self._active_response_lock:
                    self._active_response = response
                self._raise_if_cancelled()
                if 300 <= response.status_code < 400:
                    raise LLMOutboundRequestError(
                        "confirmed endpoint returned a redirect; it was not followed",
                        status_code=response.status_code,
                    )
                if response.status_code >= 400:
                    raise LLMOutboundRequestError(
                        "confirmed provider request failed; it was not retried",
                        status_code=response.status_code,
                    )
                try:
                    data = response.json()
                    if type(data) is not dict:
                        raise ValueError("provider response is not a JSON object")
                    return _parse_completion(data, fallback_model=initial_entry.id)
                except LLMApiError:
                    raise LLMOutboundRequestError(
                        "confirmed provider returned an invalid completion; it was not retried",
                        status_code=response.status_code,
                    ) from None
                except Exception:
                    raise LLMOutboundRequestError(
                        "confirmed provider returned an invalid response; it was not retried",
                        status_code=response.status_code,
                    ) from None
        except (LLMOutboundConfigurationChangedError, LLMOutboundRequestError, LLMCancelledError):
            raise
        except httpx.TimeoutException:
            raise LLMOutboundRequestError("confirmed request timed out; it was not retried") from None
        except httpx.HTTPError:
            raise LLMOutboundRequestError("confirmed request transport failed; it was not retried") from None
        except Exception:
            raise LLMOutboundRequestError("confirmed request failed; it was not retried") from None
        finally:
            with self._active_response_lock:
                if response is not None and self._active_response is response:
                    self._active_response = None
                if client is not None and self._active_client is client:
                    self._active_client = None

    def chat_confirmed_json_batch(
        self, messages_batches: list[list[dict[str, Any]]], *, model_id: str,
        session_id: str, expected_preview_json: str,
        confirm_fn: Callable[[str, bool], str], before_each: Callable[[], bool],
        response_validator: Callable[[int, LLMResponse], bool],
    ) -> tuple[LLMResponse, ...]:
        """Confirm the complete frozen batch once, then send each body at most once.

        before_each is a trusted host check (no arguments, exactly True to proceed),
        called after approval and again immediately before each POST. It must check
        the bound session and ledger; this client cannot authenticate those facts.
        response_validator(index, response) must return exactly True before the next
        batch; it is responsible for validating response identity and content.
        Failures expose only attempt counts, not partial results or request content.
        An attempted request may have reached the provider even on failure.
        """
        attempted = 0
        total = len(messages_batches) if type(messages_batches) is list else 0
        try:
            self._raise_if_cancelled()
            if (type(messages_batches) is not list or not 1 <= total <= 32
                    or type(model_id) is not str or model_id != model_id.strip()
                    or not _confirmed_label(model_id) or type(session_id) is not str
                    or not _confirmed_label(session_id) or type(expected_preview_json) is not str
                    or len(expected_preview_json.encode('utf-8')) > 2 * 1024 * 1024
                    or not callable(confirm_fn) or not callable(before_each)
                    or not callable(response_validator)):
                raise ValueError('invalid inputs')
            frozen = _clone_json_value(messages_batches)
            if any(type(batch) is not list or any(type(m) is not dict for m in batch) for batch in frozen):
                raise ValueError('invalid messages')
            frozen = [_provider_messages(batch) for batch in frozen]
            initial = ModelRegistry.load(self.paths).resolve(model_id)
            if (type(initial) is not ModelEntry or initial.id != model_id
                    or not _confirmed_label(initial.id) or not _confirmed_label(initial.vendor)
                    or not _confirmed_label(initial.provider_model)):
                raise ValueError('invalid profile')
            endpoint, url = _confirmed_endpoint(initial)
            payloads = [{"model": initial.provider_model, "messages": batch,
                         "temperature": 0.0, "stream": False,
                         "response_format": {"type": "json_object"}} for batch in frozen]
            bodies = tuple(_canonical_json_bytes(payload) for payload in payloads)
            preview = _canonical_json_bytes({"model_id": initial.id, "vendor": initial.vendor,
                "endpoint": endpoint, "session_id": session_id, "provider_payloads": payloads}).decode('utf-8')
            if preview != expected_preview_json or len(preview.encode('utf-8')) > 2 * 1024 * 1024:
                raise ValueError('preview mismatch')
            key = initial.resolve_api_key(self.paths)
            if type(key) is not str or not key:
                raise ValueError('missing credential')
            choice = confirm_fn(preview, False)
            if type(choice) is not str or choice != 'y':
                raise ValueError('not approved')

            def check() -> None:
                self._raise_if_cancelled()
                current = ModelRegistry.load(self.paths).resolve(model_id)
                if type(current) is not ModelEntry or current != initial or current.id != model_id:
                    raise ValueError('profile changed')
                if _confirmed_endpoint(current) != (endpoint, url) or current.resolve_api_key(self.paths) != key:
                    raise ValueError('configuration changed')
                if before_each() is not True:
                    raise ValueError('host check failed')
                self._raise_if_cancelled()
                # A trusted host callback can take time or mutate configuration.
                current = ModelRegistry.load(self.paths).resolve(model_id)
                if (type(current) is not ModelEntry or current != initial
                        or _confirmed_endpoint(current) != (endpoint, url)
                        or current.resolve_api_key(self.paths) != key):
                    raise ValueError('configuration changed')
                self._raise_if_cancelled()

            check()
            results = []
            for index, body in enumerate(bodies):
                check()
                client = None
                response = None
                try:
                    client = httpx.Client(timeout=self.config.timeout_sec, trust_env=False, follow_redirects=False)
                    with self._active_response_lock:
                        self._active_client = client
                    with client:
                        self._raise_if_cancelled()
                        attempted += 1
                        response = client.post(endpoint, headers={"Authorization": f"Bearer {key}",
                            "Content-Type": "application/json"}, content=body, follow_redirects=False)
                        with self._active_response_lock:
                            self._active_response = response
                        self._raise_if_cancelled()
                        if not 200 <= response.status_code < 300:
                            raise ValueError('provider status')
                        data = response.json()
                        if type(data) is not dict:
                            raise ValueError('invalid response')
                        parsed = _parse_completion(data, fallback_model=initial.id)
                        if response_validator(index, parsed) is not True:
                            raise ValueError("response validation failed")
                        results.append(parsed)
                finally:
                    with self._active_response_lock:
                        if response is not None and self._active_response is response:
                            self._active_response = None
                        if client is not None and self._active_client is client:
                            self._active_client = None
            return tuple(results)
        except Exception:
            raise LLMConfirmedBatchError(attempted=attempted, remaining=max(0, total - attempted)) from None

    def chat_confirmed_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        model_id: str,
        session_id: str,
        scope: dict[str, Any],
        tools: list[dict[str, Any]],
        confirm_fn: Callable[[str, bool], str],
        before_each: Callable[[], bool],
        on_request_attempt: Callable[[], None] | None = None,
    ) -> LLMResponse:
        """Freeze, preview, confirm, and send one tools-enabled request exactly once.

        The preview is generated from the same canonical provider body bytes that
        are POSTed. `before_each` is a trusted host check run after approval; it
        must return exactly True and should revalidate the project binding plus
        the identities/content of every source included in the messages.
        """
        self._raise_if_cancelled()
        if (type(model_id) is not str or model_id != model_id.strip()
                or not _confirmed_label(model_id) or type(session_id) is not str
                or not _confirmed_label(session_id) or type(scope) is not dict
                or type(messages) is not list or type(tools) is not list or not tools
                or not callable(confirm_fn) or not callable(before_each)
                or (on_request_attempt is not None and not callable(on_request_attempt))):
            raise LLMOutboundConfigurationChangedError("confirmed tool request inputs are invalid")
        try:
            frozen_messages = _clone_json_value(_provider_messages(messages))
            frozen_tools = _clone_json_value(tools)
            frozen_scope = _clone_json_value(scope)
            if (type(frozen_messages) is not list
                    or any(type(item) is not dict for item in frozen_messages)
                    or type(frozen_tools) is not list or not frozen_tools
                    or any(type(item) is not dict for item in frozen_tools)
                    or type(frozen_scope) is not dict):
                raise ValueError("request values must be JSON objects")
            initial_entry = ModelRegistry.load(self.paths).resolve(model_id)
            if (type(initial_entry) is not ModelEntry or initial_entry.id != model_id
                    or not initial_entry.supports_tool_call
                    or not _confirmed_label(initial_entry.id)
                    or not _confirmed_label(initial_entry.vendor)
                    or not _confirmed_label(initial_entry.provider_model)):
                raise ValueError("tools-capable model profile is unavailable")
            endpoint_text, endpoint_url = _confirmed_endpoint(initial_entry)
            provider_payload = {
                "model": initial_entry.provider_model,
                "messages": frozen_messages,
                "temperature": 0.0,
                "stream": False,
                "tools": frozen_tools,
            }
            body = _canonical_json_bytes(provider_payload)
            preview = _canonical_json_bytes({
                "method": "POST",
                "model_id": initial_entry.id,
                "vendor": initial_entry.vendor,
                "endpoint": endpoint_text,
                "headers": {"Authorization": "[redacted]", "Content-Type": "application/json"},
                "session_id": session_id,
                "scope": frozen_scope,
                "provider_payload": provider_payload,
            }).decode("utf-8")
            if len(body) > 2 * 1024 * 1024 or len(preview.encode("utf-8")) > 2 * 1024 * 1024:
                raise ValueError("confirmed tool request exceeds preview limit")
            first_api_key = initial_entry.resolve_api_key(self.paths)
        except LLMMissingApiKeyError:
            raise
        except Exception:
            raise LLMOutboundConfigurationChangedError(
                "confirmed tool request configuration is invalid"
            ) from None
        if type(first_api_key) is not str or not first_api_key:
            raise LLMMissingApiKeyError(f"API key not set for model {initial_entry.id}")

        try:
            choice = confirm_fn(preview, False)
        except Exception:
            raise LLMOutboundConfirmationError("confirmed tool request was not approved") from None
        if type(choice) is not str or choice != "y":
            raise LLMOutboundConfirmationError("confirmed tool request was not approved")
        self._raise_if_cancelled()
        try:
            if before_each() is not True:
                raise ValueError("host scope check failed")
            self._raise_if_cancelled()
            current_entry = ModelRegistry.load(self.paths).resolve(model_id)
            if (type(current_entry) is not ModelEntry or current_entry != initial_entry
                    or current_entry.id != model_id or not current_entry.supports_tool_call):
                raise ValueError("model registry changed")
            current_endpoint, current_url = _confirmed_endpoint(current_entry)
            if current_endpoint != endpoint_text or current_url != endpoint_url:
                raise ValueError("model endpoint changed")
            current_payload = {
                "model": current_entry.provider_model,
                "messages": frozen_messages,
                "temperature": 0.0,
                "stream": False,
                "tools": frozen_tools,
            }
            if _canonical_json_bytes(current_payload) != body:
                raise ValueError("frozen provider payload changed")
            current_api_key = current_entry.resolve_api_key(self.paths)
            if (type(current_api_key) is not str or not current_api_key
                    or current_api_key != first_api_key):
                raise ValueError("credential changed")
            self._raise_if_cancelled()
        except Exception:
            raise LLMOutboundConfigurationChangedError(
                "confirmed tool request scope or configuration changed; no request was sent"
            ) from None

        client: httpx.Client | None = None
        response: httpx.Response | None = None
        try:
            client = httpx.Client(
                timeout=self.config.timeout_sec,
                trust_env=False,
                follow_redirects=False,
            )
            with self._active_response_lock:
                if self._active_client is not None:
                    raise LLMOutboundConfigurationChangedError("LLM client is already active")
                self._active_client = client
            with client:
                self._raise_if_cancelled()
                try:
                    final_scope_check = before_each()
                except Exception:
                    final_scope_check = False
                if final_scope_check is not True:
                    raise LLMOutboundConfigurationChangedError(
                        "confirmed tool request scope changed; no request was sent"
                    )
                self._raise_if_cancelled()
                final_entry = ModelRegistry.load(self.paths).resolve(model_id)
                if (type(final_entry) is not ModelEntry or final_entry != initial_entry
                        or _confirmed_endpoint(final_entry) != (endpoint_text, endpoint_url)
                        or final_entry.resolve_api_key(self.paths) != first_api_key):
                    raise LLMOutboundConfigurationChangedError(
                        "confirmed tool model configuration changed; no request was sent"
                    )
                self._raise_if_cancelled()
                if on_request_attempt is not None:
                    on_request_attempt()
                response = client.post(
                    endpoint_text,
                    headers={"Authorization": f"Bearer {current_api_key}",
                             "Content-Type": "application/json"},
                    content=body,
                    follow_redirects=False,
                )
                with self._active_response_lock:
                    self._active_response = response
                self._raise_if_cancelled()
                if 300 <= response.status_code < 400:
                    raise LLMOutboundRequestError(
                        "confirmed tool endpoint returned a redirect; it was not followed",
                        status_code=response.status_code,
                    )
                if not 200 <= response.status_code < 300:
                    raise LLMOutboundRequestError(
                        "confirmed tool request failed; it was not retried",
                        status_code=response.status_code,
                    )
                try:
                    data = response.json()
                    if type(data) is not dict:
                        raise ValueError("invalid response")
                    return _parse_completion(data, fallback_model=initial_entry.id)
                except Exception:
                    raise LLMOutboundRequestError(
                        "confirmed tool response was invalid; it was not retried",
                        status_code=response.status_code,
                    ) from None
        except (LLMOutboundConfigurationChangedError, LLMOutboundRequestError, LLMCancelledError):
            raise
        except httpx.TimeoutException:
            raise LLMOutboundRequestError("confirmed tool request timed out; it was not retried") from None
        except httpx.HTTPError:
            raise LLMOutboundRequestError("confirmed tool transport failed; it was not retried") from None
        except Exception:
            raise LLMOutboundRequestError("confirmed tool request failed; it was not retried") from None
        finally:
            with self._active_response_lock:
                if response is not None and self._active_response is response:
                    self._active_response = None
                if client is not None and self._active_client is client:
                    self._active_client = None

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
