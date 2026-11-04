from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import build_builtin_tool_definition
from session import create_new
from tools.executor import ExecutorSession, ToolExecutor, _tool_event_args, build_confirm_preview
from tools.registry import ToolRegistry
from tools.schema import tool_ok
from tests.isolation_helpers import make_temp_agent_paths


class GenerateVideoReferenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.paths = make_temp_agent_paths(self)
        (self.paths.workspace / "demo-project").mkdir(parents=True)
        self.session = create_new(self.paths, conversation_id="video-reference")
        self.session.set_goal("video")
        self.session.persist_goal()
        self.registry = ToolRegistry.load(self.paths)
        self.previews = []
        self.executor = ToolExecutor(
            registry=self.registry,
            session=ExecutorSession(session_dir=self.session.session_dir, project_root="workspace/demo-project", active_shell="project"),
            confirm_fn=lambda text, _all: self.previews.append(text) or "y",
        )
        self.image = self.paths.workspace / "demo-project" / "reference.png"
        self.image.write_bytes(bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"fixture")

    def test_schema_documents_explicit_reference_and_search_fields(self):
        props = build_builtin_tool_definition("generate_video")["function"]["parameters"]["properties"]
        self.assertIn("reference_image_path", props)
        self.assertIn("reference_context", props)
        self.assertIn("reference_id", props)

    def test_path_reference_is_authorized_confirmed_and_passed_to_worker(self):
        seen = []
        with patch("tools.executor._BUILTIN_RUNNERS", {**__import__("tools.executor", fromlist=["_BUILTIN_RUNNERS"])._BUILTIN_RUNNERS,
                  "generate_video": lambda args, **kw: seen.append((args, kw)) or tool_ok("generate_video", {"status": "queued"})}):
            result = self.executor.run("generate_video", {"prompt": "animate", "reference_image_path": "workspace/demo-project/reference.png"})
        self.assertTrue(result.ok, result)
        self.assertIn("workspace/demo-project/reference.png", self.previews[0])
        self.assertIn("explicit local image path", self.previews[0].lower())
        self.assertEqual(seen[0][1]["reference_image_path"], "workspace/demo-project/reference.png")
        self.assertIsNone(seen[0][1]["prepared_reference"])

    def test_absolute_agent_root_path_is_normalized_and_sent_as_relative_string(self):
        seen = []
        with patch("tools.executor._BUILTIN_RUNNERS", {**__import__("tools.executor", fromlist=["_BUILTIN_RUNNERS"])._BUILTIN_RUNNERS,
                  "generate_video": lambda args, **kw: seen.append((args, kw)) or tool_ok("generate_video", {})}):
            result = self.executor.run("generate_video", {"prompt": "animate", "reference_image_path": str(self.image.resolve()).replace("\\", "/")})
        self.assertTrue(result.ok, result)
        self.assertEqual(seen[0][1]["reference_image_path"], "workspace/demo-project/reference.png")
        self.assertIsInstance(seen[0][1]["reference_image_path"], str)
        self.assertIn(str(self.image.resolve()), self.previews[-1])

    def test_reference_path_cannot_mix_with_search_fields_or_request_id(self):
        for args in (
            {"prompt": "x", "reference_image_path": "workspace/demo-project/reference.png", "reference_context": "current_attachment"},
            {"prompt": "x", "reference_image_path": "workspace/demo-project/reference.png", "reference_id": "img-1"},
            {"prompt": "x", "reference_image_path": "workspace/demo-project/reference.png", "request_id": "job-1"},
            {"reference_image_path": "workspace/demo-project/reference.png", "request_id": "job-1"},
        ):
            self.assertIsNotNone(self.executor.validate("generate_video", args), args)

    def test_unauthorized_or_non_png_path_fails_before_confirmation_and_dispatch(self):
        outside = self.paths.agent_root.parent / "outside.png"
        outside.write_bytes(bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"fixture")
        for path in ("../outside.png", "workspace/demo-project/missing.png", str(outside.resolve())):
            result = self.executor.run("generate_video", {"prompt": "x", "reference_image_path": path})
            self.assertFalse(result.ok)
        self.assertEqual(self.previews, [])

    def test_search_reference_selection_and_confirm_preview_show_source(self):
        from image_references import ImageReference, ImageReferenceSearch
        selected = ImageReference("img-abc", self.image, "workspace/demo-project/reference.png", "reference.png", "image/png", "s1", "Source conversation", "now", "user asked to animate", 0, 0)
        with patch("image_references.find_image_references", return_value=ImageReferenceSearch("selected", selected=selected)):
            seen = []
            with patch("tools.executor._BUILTIN_RUNNERS", {**__import__("tools.executor", fromlist=["_BUILTIN_RUNNERS"])._BUILTIN_RUNNERS,
                      "generate_video": lambda args, **kw: seen.append((args, kw)) or tool_ok("generate_video", {})}):
                result = self.executor.run("generate_video", {"prompt": "x", "reference_context": "prior kite image"})
        self.assertTrue(result.ok, result)
        self.assertIn("Source conversation", self.previews[-1])
        self.assertIn("reference.png", self.previews[-1])
        self.assertIs(seen[0][1]["prepared_reference"], selected)
        self.assertIsNone(seen[0][1]["reference_image_path"])

    def test_ambiguous_search_prompts_selection_without_sending(self):
        from image_references import ImageReference, ImageReferenceSearch
        one = ImageReference("img-1", self.image, "a.png", "a.png", "image/png", "s1", "A", "", "", 0, 0)
        two = ImageReference("img-2", self.image, "b.png", "b.png", "image/png", "s2", "B", "", "", 0, 0)
        with patch("image_references.find_image_references", return_value=ImageReferenceSearch("ambiguous", candidates=(one, two))):
            with patch("tools.executor._BUILTIN_RUNNERS") as runners:
                result = self.executor.run("generate_video", {"prompt": "x", "reference_context": "old image"})
        self.assertEqual(result.data["status"], "reference_selection_required")
        self.assertIn("img-1", str(result.data))
        runners.__getitem__.assert_not_called()
        self.assertEqual(self.previews, [])

    def test_request_id_resume_never_touches_reference_and_event_args_redact_path(self):
        args = {"request_id": "job-1", "reference_image_path": "workspace/demo-project/reference.png"}
        self.assertIsNotNone(self.executor.validate("generate_video", args))
        self.assertNotIn("reference_image_path", _tool_event_args("generate_video", {"prompt": "x", "reference_image_path": "secret-path"}))
        self.assertNotIn("base64-secret", str(_tool_event_args("generate_video", {"prompt": "x", "content_base64": "base64-secret"})))

    def test_preview_marks_reference_origin_and_full_path(self):
        preview = build_confirm_preview("generate_video", {"prompt": "x", "reference_image_path": "workspace/pic.png"}, agent_paths=self.paths)
        self.assertIn("workspace/pic.png", preview)
        self.assertIn("explicit local image path", preview.lower())


if __name__ == "__main__":
    unittest.main()
