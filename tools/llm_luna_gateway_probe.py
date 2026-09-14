"""Compare Luna gateway behavior: tools + reasoning_effort (0x567 vs Tokeness).

Usage:
  .venv\\Scripts\\python.exe tools\\llm_luna_gateway_probe.py --provider 0x567
  .venv\\Scripts\\python.exe tools\\llm_luna_gateway_probe.py --provider tokeness

Requires API key in env or data/llm_secrets.json (OX567_API_KEY / LLM_tokeness_KEY).
See docs/LLM-LUNA-GATEWAYS.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
AGENT_CORE = ROOT / "agent-core"
if str(AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(AGENT_CORE))

from llm_models import TOKENESS_API_KEY_ENVS, TOKENESS_DEFAULT_BASE_URL, get_registry
from llm_secrets import get_llm_secret
from paths import AgentPaths

PROVIDERS = {
    "0x567": {
        "registry_id": "0x567-flash",
        "key_envs": ("OX567_API_KEY",),
    },
    "tokeness": {
        "registry_id": "tokeness-luna",
        "key_envs": TOKENESS_API_KEY_ENVS,
    },
}

SAMPLE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ping",
            "description": "health check",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }
]


def _resolve_key(paths: AgentPaths, env_names: tuple[str, ...]) -> tuple[str | None, str | None]:
    import os

    for env_name in env_names:
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip(), env_name
        stored = get_llm_secret(env_name, paths)
        if stored:
            return stored, env_name
    return None, None


def _truncate(text: str, limit: int = 480) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _post_json(client: httpx.Client, url: str, api_key: str, payload: dict[str, Any]) -> tuple[int, str]:
    try:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        return response.status_code, _truncate(response.text)
    except httpx.TimeoutException:
        return 0, "ReadTimeout"
    except httpx.HTTPError as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _stream_probe(client: httpx.Client, url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["stream"] = True
    payload.setdefault("max_tokens", 64)
    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    tool_call_deltas = 0
    status = 0
    error_body = ""
    try:
        with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        ) as response:
            status = response.status_code
            if status >= 400:
                error_body = _truncate(response.read().decode("utf-8", errors="replace"))
                return {
                    "status": status,
                    "error": error_body,
                    "reasoning_len": 0,
                    "content": "",
                    "tool_call_deltas": 0,
                }
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                for key in ("reasoning_content", "reasoning"):
                    val = delta.get(key)
                    if isinstance(val, str) and val:
                        reasoning_parts.append(val)
                content = delta.get("content")
                if isinstance(content, str) and content:
                    content_parts.append(content)
                if delta.get("tool_calls"):
                    tool_call_deltas += 1
    except httpx.TimeoutException:
        return {
            "status": 0,
            "error": "ReadTimeout",
            "reasoning_len": 0,
            "content": "",
            "tool_call_deltas": 0,
        }
    except httpx.HTTPError as exc:
        return {
            "status": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "reasoning_len": 0,
            "content": "",
            "tool_call_deltas": 0,
        }
    reasoning = "".join(reasoning_parts)
    return {
        "status": status,
        "reasoning_len": len(reasoning),
        "reasoning_sample": _truncate(reasoning, 160),
        "content": _truncate("".join(content_parts), 120),
        "tool_call_deltas": tool_call_deltas,
    }


def run_probe(provider: str) -> int:
    provider_key = provider.casefold()
    if provider_key not in PROVIDERS:
        print(f"unknown provider: {provider!r} (use 0x567 or tokeness)")
        return 2

    spec = PROVIDERS[provider_key]
    paths = AgentPaths.discover()
    api_key, key_source = _resolve_key(paths, spec["key_envs"])
    if not api_key:
        env_hint = " / ".join(spec["key_envs"])
        print(f"[SKIP] no API key for {provider_key} (set {env_hint})")
        return 1

    entry = get_registry().get(spec["registry_id"])
    if entry is None:
        print(f"[FAIL] registry missing {spec['registry_id']}")
        return 2

    url = entry.chat_completions_url()
    model = entry.provider_model
    print(f"provider={provider_key} model={model}")
    print(f"url={url}")
    print(f"key_source={key_source}")
    print()

    base_msgs = [{"role": "user", "content": "Reply with exactly: ok"}]
    stream_msgs = [
        {
            "role": "user",
            "content": "Think briefly, then call ping if useful, else reply ok.",
        }
    ]

    sync_cases: list[tuple[str, dict[str, Any]]] = [
        (
            "sync_tools+reasoning_medium",
            {
                "model": model,
                "messages": base_msgs,
                "tools": SAMPLE_TOOLS,
                "reasoning_effort": "medium",
                "max_tokens": 16,
            },
        ),
        (
            "sync_tools+reasoning_none",
            {
                "model": model,
                "messages": base_msgs,
                "tools": SAMPLE_TOOLS,
                "reasoning_effort": "none",
                "max_tokens": 16,
            },
        ),
        (
            "sync_tools_only",
            {
                "model": model,
                "messages": base_msgs,
                "tools": SAMPLE_TOOLS,
                "max_tokens": 16,
            },
        ),
        (
            "sync_no_tools+reasoning_medium",
            {
                "model": model,
                "messages": base_msgs,
                "reasoning_effort": "medium",
                "max_tokens": 16,
            },
        ),
    ]

    stream_cases: list[tuple[str, dict[str, Any]]] = [
        (
            "stream_tools+reasoning_medium",
            {"messages": stream_msgs, "tools": SAMPLE_TOOLS, "reasoning_effort": "medium"},
        ),
        (
            "stream_tools+reasoning_none",
            {"messages": stream_msgs, "tools": SAMPLE_TOOLS, "reasoning_effort": "none"},
        ),
        ("stream_tools_only", {"messages": stream_msgs, "tools": SAMPLE_TOOLS}),
    ]

    exit_code = 0
    with httpx.Client(timeout=60.0) as client:
        for name, payload in sync_cases:
            status, body = _post_json(client, url, api_key, payload)
            print(f"=== {name} ===")
            print(f"status={status}")
            print(body)
            print()
            if status == 0 or status >= 400:
                exit_code = 1

        for name, payload in stream_cases:
            result = _stream_probe(client, url, api_key, {"model": model, **payload})
            print(f"=== {name} ===")
            for key, value in result.items():
                print(f"{key}={value}")
            print()
            status = int(result.get("status", 0))
            if status == 0 or status >= 400:
                exit_code = 1

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Luna gateways for tools + reasoning_effort.")
    parser.add_argument(
        "--provider",
        choices=sorted(PROVIDERS.keys()),
        default="0x567",
        help="Gateway to probe (default: 0x567)",
    )
    args = parser.parse_args()
    raise SystemExit(run_probe(args.provider))


if __name__ == "__main__":
    main()
