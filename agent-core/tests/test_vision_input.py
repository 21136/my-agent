"""Multimodal image attachment input tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from context import build_llm_messages, has_image_attachment
from file_stage import MAX_IMAGE_INPUT_BYTES, StagedAttachment, compose_user_message
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class VisionInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.session = create_new(self.paths, conversation_id="_vision_input")

    def _attachment(
        self,
        *,
        name: str = "screen.png",
        mime: str = "image/png",
        size: int = 6,
    ) -> StagedAttachment:
        ref = f"workspace/{name}"
        path = self.paths.resolve_under_agent(ref, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n" + b"x" * max(0, size - 6))
        return StagedAttachment(
            id="image-1",
            name=name,
            ref=ref,
            size=size,
            mime=mime,
            readable_text=False,
            copied=False,
        )

    def test_image_attachment_metadata_marks_image_input(self) -> None:
        item = self._attachment()
        self.assertTrue(item.to_item()["image_input"])
        self.assertIn("图片附件", compose_user_message(text="看图", attachments=[item]))

    def test_supported_model_payload_contains_image_data_uri(self) -> None:
        item = self._attachment()
        self.session.messages.append(
            {"role": "user", "content": compose_user_message(text="看图", attachments=[item])}
        )

        messages = build_llm_messages(self.session, include_images=True)
        content = messages[0]["content"]
        self.assertIsInstance(content, list)
        assert isinstance(content, list)
        image_block = next(block for block in content if block.get("type") == "image_url")
        self.assertTrue(image_block["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_unsupported_model_payload_stays_text(self) -> None:
        item = self._attachment()
        self.session.messages.append(
            {"role": "user", "content": compose_user_message(text="看图", attachments=[item])}
        )

        messages = build_llm_messages(self.session, include_images=False)
        self.assertIsInstance(messages[0]["content"], str)
        self.assertTrue(has_image_attachment(self.session.messages))

    def test_oversized_image_is_not_injected(self) -> None:
        item = self._attachment(size=MAX_IMAGE_INPUT_BYTES + 1)
        self.session.messages.append(
            {"role": "user", "content": compose_user_message(text="看图", attachments=[item])}
        )

        messages = build_llm_messages(self.session, include_images=True)
        self.assertIsInstance(messages[0]["content"], str)

    def test_non_image_attachment_is_not_injected_as_image(self) -> None:
        item = self._attachment(name="notes.txt", mime="text/plain")
        self.session.messages.append(
            {"role": "user", "content": compose_user_message(text="读一下", attachments=[item])}
        )

        messages = build_llm_messages(self.session, include_images=True)
        self.assertIsInstance(messages[0]["content"], str)


if __name__ == "__main__":
    unittest.main()
