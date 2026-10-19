"""PDF rasterization for multimodal vision input."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from context import build_llm_messages, has_vision_attachment
from file_stage import StagedAttachment, compose_user_message
from pdf_vision import render_pdf_page_data_uris
from session import create_new
from tests.isolation_helpers import make_temp_agent_paths


class PdfVisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.session = create_new(self.paths, conversation_id="_pdf_vision")

    def _write_sample_pdf(self, ref: str) -> Path:
        import fitz

        path = self.paths.resolve_under_agent(ref, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Resume line for vision test", fontsize=14)
        doc.save(path)
        doc.close()
        return path

    def test_render_pdf_produces_data_uris(self) -> None:
        path = self._write_sample_pdf("workspace/sample.pdf")
        uris = render_pdf_page_data_uris(path)
        self.assertEqual(len(uris), 1)
        self.assertTrue(uris[0].startswith("data:image/jpeg;base64,"))

    def test_pdf_attachment_injects_images_when_model_supports_vision(self) -> None:
        ref = "workspace/resume.pdf"
        self._write_sample_pdf(ref)
        item = StagedAttachment(
            id="pdf-1",
            name="resume.pdf",
            ref=ref,
            size=self.paths.resolve_under_agent(ref).stat().st_size,
            mime="application/pdf",
            readable_text=True,
            copied=False,
        )
        self.assertTrue(item.vision_input)
        self.session.messages.append(
            {
                "role": "user",
                "content": compose_user_message(text="读简历", attachments=[item]),
            }
        )
        self.assertTrue(has_vision_attachment(self.session.messages))
        messages = build_llm_messages(self.session, include_images=True)
        content = messages[0]["content"]
        self.assertIsInstance(content, list)
        assert isinstance(content, list)
        images = [b for b in content if b.get("type") == "image_url"]
        self.assertGreaterEqual(len(images), 1)
        self.assertEqual(images[0]["image_url"]["detail"], "high")


if __name__ == "__main__":
    unittest.main()
