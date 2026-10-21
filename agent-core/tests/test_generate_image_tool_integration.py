"""Built-in registration, history selection, and paid-call confirmation tests."""

from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import httpx
from agent import build_builtin_tool_definition, build_llm_tools
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tests.isolation_helpers import make_temp_agent_paths

PNG_BYTES = b"\x89PNG\r\n\x1a\nimage-fixture"


class GenerateImageToolIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        (self.paths.workspace / "demo-project").mkdir(parents=True)
        self.registry = ToolRegistry.load(self.paths)

    def _new_session(self, conversation_id: str, title: str, message: str | None = None):
        session = create_new(self.paths, conversation_id=conversation_id)
        session.set_goal(title)
        session.persist_goal()
        if message is not None:
            session.append_message({"role": "user", "content": message})
        return session

    def _attach_image(self, session, filename: str, context: str) -> str:
        ref = f"workspace/_drops/{session.conversation_id}/{filename}"
        path = self.paths.resolve_under_agent(ref, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG_BYTES)
        content = (
            "[附件]\n"
            f"- {filename} → {ref} (1.0 KB, image/png；不可直接 read_file；图片附件)\n\n"
            f"{context}"
        )
        session.append_message({"role": "user", "content": content})
        return ref

    def _executor(self, session, confirm_fn):
        return ToolExecutor(
            registry=self.registry,
            session=ExecutorSession(
                session_dir=session.session_dir,
                project_root="workspace/demo-project",
                project_id="demo-project",
                turn_mode=session.meta.turn_mode,
                ordinary_work_intent=getattr(session, "_ordinary_work_intent", "execute"),
                active_shell="project",
            ),
            confirm_fn=confirm_fn,
        )

    def test_builtin_schema_is_registered_and_request_mode_controls_execution(self) -> None:
        builtin = self.registry.get_builtin("generate_image")
        self.assertIsNotNone(builtin)
        self.assertTrue(builtin.confirm)

        definition = build_builtin_tool_definition("generate_image")
        self.assertEqual(definition["function"]["parameters"]["required"], ["prompt"])
        self.assertIn("reference_context", definition["function"]["parameters"]["properties"])
        self.assertIn("reference_id", definition["function"]["parameters"]["properties"])

        session = self._new_session("ask-chat", "Ask mode")
        session.meta.turn_mode = "ask"
        tool_names = {item["function"]["name"] for item in build_llm_tools(session, registry=self.registry)}
        self.assertNotIn("generate_image", tool_names)

        read_only = self._new_session("read-only-chat", "Read-only mode")
        read_only._ordinary_work_intent = "read_only"
        read_only_tools = {
            item["function"]["name"]
            for item in build_llm_tools(read_only, registry=self.registry)
        }
        self.assertIn("generate_image", read_only_tools)

        executor = self._executor(session, lambda *_: "y")
        rejected = executor.validate("generate_image", {"prompt": "a tree"})
        self.assertIsNotNone(rejected)
        self.assertFalse(rejected.ok)
        read_only_executor = self._executor(read_only, lambda *_: "y")
        read_only_rejected = read_only_executor.validate("generate_image", {"prompt": "a tree"})
        self.assertIsNotNone(read_only_rejected)
        self.assertEqual(read_only_rejected.error.code, "permission_denied")

    def test_ambiguous_history_match_returns_candidates_without_confirm_or_http(self) -> None:
        current = self._new_session("current-chat", "Image request", "Use a flower image from our old chats")
        first = self._new_session("garden-chat-a", "Spring garden")
        second = self._new_session("garden-chat-b", "Garden reference")
        self._attach_image(first, "blossom-a.png", "这是花园里的樱花。")
        self._attach_image(second, "blossom-b.png", "这是花园里的樱花。")
        confirms: list[str] = []
        executor = self._executor(current, lambda preview, _allow: confirms.append(preview) or "y")

        with patch("tools.builtin.generate_image.httpx.post") as post:
            result = executor.run(
                "generate_image",
                {"prompt": "turn this into watercolor", "reference_context": "花园里的樱花"},
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["status"], "reference_selection_required")
        self.assertEqual({c["filename"] for c in result.data["candidates"]}, {"blossom-a.png", "blossom-b.png"})
        self.assertEqual(confirms, [])
        post.assert_not_called()
        self.assertNotIn(str(self.paths.workspace), json.dumps(result.data, ensure_ascii=False))

    def test_rejected_confirmation_shows_exact_source_and_never_calls_provider(self) -> None:
        current = self._new_session("current-chat", "Image request")
        old = self._new_session("trip-chat", "日月潭周末旅行")
        self._attach_image(old, "lake.png", "这是日月潭湖边的照片。")
        previews: list[str] = []
        executor = self._executor(current, lambda preview, _allow: previews.append(preview) or "n")

        with patch("tools.builtin.generate_image.httpx.post") as post:
            result = executor.run(
                "generate_image",
                {"prompt": "paint this as watercolor", "reference_context": "日月潭湖边照片"},
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "confirm_rejected")
        self.assertEqual(len(previews), 1)
        self.assertTrue(previews[0].startswith("Reference: lake.png from"))
        self.assertIn("日月潭周末旅行", previews[0])
        self.assertIn("lake.png", previews[0])
        self.assertIn("日月潭", previews[0])
        self.assertIn("sent to 0x567 only after approval", previews[0])
        self.assertNotIn(str(self.paths.workspace), previews[0])
        post.assert_not_called()

    def test_selected_current_attachment_is_sent_only_after_confirmation(self) -> None:
        current = self._new_session("current-chat", "Current image", "Use this image")
        self._attach_image(current, "current.png", "当前消息的参考图。")
        previews: list[str] = []
        executor = self._executor(current, lambda preview, _allow: previews.append(preview) or "y")
        executor.session.workspace_evolved_approved = True
        executor.session.ordinary_visible_confirms_this_turn = 99
        image_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
        response = httpx.Response(200, json={"data": [{"b64_json": image_b64}]})

        with patch.dict(os.environ, {"LLM_0x567_KEY": "integration-test-secret"}), patch(
            "tools.builtin.generate_image.httpx.post", return_value=response
        ) as post:
            result = executor.run(
                "generate_image",
                {"prompt": "make a sticker", "reference_context": "current_attachment"},
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.data["used_reference_image"])
        self.assertEqual(result.data["reference_filename"], "current.png")
        self.assertEqual(post.call_count, 1)
        self.assertIn("current.png", previews[0])
        self.assertIn("will be sent to 0x567 only after approval", previews[0])
        self.assertNotIn("integration-test-secret", previews[0])


if __name__ == "__main__":
    unittest.main()
