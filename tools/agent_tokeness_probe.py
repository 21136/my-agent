"""Send an agent-like Tokeness request (full tools + reasoning_effort) like Desktop.

Usage:
  $env:LLM_tokeness_KEY = "sk-..."   # or save in Desktop「模型密钥」
  .venv\\Scripts\\python.exe tools\\agent_tokeness_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_CORE = ROOT / "agent-core"
if str(AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(AGENT_CORE))

from llm_client import LLMClient, LLMError, load_config
from llm_models import get_registry
from tools.registry import ToolRegistry


def main() -> int:
    entry = get_registry().get("tokeness-luna")
    if entry is None:
        print("[FAIL] tokeness-luna not in registry")
        return 2
    if not entry.resolve_api_key():
        print("[FAIL] no Tokeness key (LLM_tokeness_KEY in env or Desktop 模型密钥)")
        return 1

    tools = ToolRegistry.load().openai_tools_payload()
    print(f"url={entry.chat_completions_url()}")
    print(f"model={entry.provider_model}")
    print(f"tools_count={len(tools)}")

    client = LLMClient(load_config())
    try:
        response = client.chat(
            [{"role": "user", "content": "Reply with exactly: ok"}],
            model="tokeness-luna",
            tools=tools,
            reasoning_effort="medium",
        )
    except LLMError as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1

    print(f"[OK] finish={response.finish_reason!r} content={response.content!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
