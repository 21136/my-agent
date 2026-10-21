"""session.history rehydrates inline chat images from tool messages."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from session import _build_chat_history_from_messages
from tools.schema import tool_ok, to_json


class SessionChatHistoryImagesTests(unittest.TestCase):
    def test_assistant_history_includes_inline_images_from_tool_results(self) -> None:
        image_path = "workspace/_drops/chat/generated-images/foo.png"
        tool_body = to_json(
            tool_ok(
                "generate_image",
                {"path": image_path, "display_paths": [image_path]},
            )
        )
        messages = [
            {"role": "user", "content": "生成背景图"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "generate_image"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": tool_body},
            {"role": "assistant", "content": "两张都生成好了。"},
        ]
        items, total = _build_chat_history_from_messages(messages)
        self.assertEqual(total, 2)
        self.assertEqual(items[0]["role"], "user")
        self.assertEqual(items[1]["role"], "assistant")
        self.assertEqual(items[1].get("inline_images"), [image_path])

    def test_legacy_generate_image_without_display_paths_uses_path(self) -> None:
        image_path = "workspace/demo/artifacts/generated-images/legacy.png"
        tool_body = json.dumps(
            {
                "ok": True,
                "tool": "generate_image",
                "data": {"path": image_path},
                "truncated": False,
                "error": None,
                "duration_ms": 1,
            }
        )
        messages = [
            {"role": "user", "content": "图"},
            {"role": "tool", "tool_call_id": "c1", "content": tool_body},
            {"role": "assistant", "content": "好了"},
        ]
        items, _ = _build_chat_history_from_messages(messages)
        self.assertEqual(items[-1].get("inline_images"), [image_path])


if __name__ == "__main__":
    unittest.main()
