"""Direct 0x567 video API runner tests; provider HTTP stays mocked."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import httpx
from image_references import ImageReference
from tools.builtin.generate_video import run
from tests.isolation_helpers import make_temp_agent_paths


class GenerateVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        (self.paths.workspace / "demo-project").mkdir(parents=True)


    def _prepared_png(self) -> ImageReference:
        image = self.paths.workspace / "reference.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture-png-data")
        return ImageReference(
            reference_id="ref-1", path=image, source_ref="workspace/reference.png",
            filename="reference.png", mime_type="image/png", session_id="s",
            session_title="test", updated_at="", context="", message_index=0,
            attachment_index=0,
        )

    def test_prepared_png_is_base64_data_url_and_generation_submits_once(self) -> None:
        import base64
        reference = self._prepared_png()
        expected = "data:image/png;base64," + base64.b64encode(reference.path.read_bytes()).decode("ascii")
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post",
            return_value=httpx.Response(200, json={"request_id": "req-image"}),
        ) as post, patch(
            "tools.builtin.generate_video.httpx.get",
            return_value=httpx.Response(200, json={"status": "processing"}),
        ), patch("tools.builtin.generate_video.POLL_TIMEOUT_SECONDS", 0):
            result = run({"prompt": "animate"}, paths=self.paths, prepared_reference=reference)
        self.assertFalse(result.ok)
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["image"], {"url": expected})
        self.assertNotIn(expected, str(result.to_dict()))
        self.assertNotIn("test-secret", str(result.to_dict()))

    def test_reference_path_is_agent_scoped_and_png_only_before_post(self) -> None:
        image = self.paths.workspace / "reference.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post", return_value=httpx.Response(200, json={"request_id": "req"})
        ) as post, patch(
            "tools.builtin.generate_video.httpx.get", return_value=httpx.Response(200, json={"status": "processing"})
        ), patch("tools.builtin.generate_video.POLL_TIMEOUT_SECONDS", 0):
            result = run({"prompt": "animate"}, paths=self.paths, reference_image_path="workspace/reference.png")
        self.assertFalse(result.ok)
        self.assertEqual(post.call_args.kwargs["json"]["image"]["url"].split(",", 1)[0], "data:image/png;base64")

        image.write_bytes(b"not png")
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post"
        ) as rejected_post:
            rejected = run({"prompt": "animate"}, paths=self.paths, reference_image_path="workspace/reference.png")
        self.assertFalse(rejected.ok)
        rejected_post.assert_not_called()

    def test_reference_sources_and_resume_request_id_cannot_be_mixed(self) -> None:
        reference = self._prepared_png()
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post"
        ) as post:
            both = run({"prompt": "animate"}, paths=self.paths, prepared_reference=reference, reference_image_path="workspace/reference.png")
            resume = run({"request_id": "req-existing"}, paths=self.paths, prepared_reference=reference)
        self.assertEqual(both.error.code, "validation_error")
        self.assertEqual(resume.error.code, "validation_error")
        post.assert_not_called()

    def test_oversized_reference_is_rejected_before_post(self) -> None:
        image = self.paths.workspace / "large.png"
        from tools.builtin.generate_video import MAX_REFERENCE_IMAGE_BYTES
        with image.open("wb") as stream:
            stream.truncate(MAX_REFERENCE_IMAGE_BYTES)
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post"
        ) as post:
            result = run({"prompt": "animate"}, paths=self.paths, reference_image_path="workspace/large.png")
        self.assertFalse(result.ok)
        post.assert_not_called()

    def test_generation_posts_once_polls_downloads_video_and_returns_attachment(self) -> None:
        poll_responses = [
            httpx.Response(200, json={"status": "processing"}),
            httpx.Response(200, json={"status": "done", "video": {"url": "https://cdn.0x567.com/result.mp4"}}),
        ]
        video = b"\x00\x00\x00\x18ftypmp42video-fixture"
        @__import__("contextlib").contextmanager
        def stream_response(_method: str, url: str, **_kwargs: object):
            response = httpx.Response(200, content=video)
            yield response

        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-video-secret"}), patch(
            "tools.builtin.generate_video.httpx.post",
            return_value=httpx.Response(200, json={"request_id": "req-" + "x" * 300}),
        ) as post, patch(
            "tools.builtin.generate_video.httpx.get",
            side_effect=[*poll_responses],
        ) as get, patch("tools.builtin.generate_video.httpx.stream", side_effect=stream_response):
            result = run({"prompt": "A quiet ocean"}, paths=self.paths, project_root="workspace/demo-project", conversation_id="chat-1")

        self.assertTrue(result.ok)
        self.assertEqual(result.data["request_id"], "req-" + "x" * 300)
        self.assertEqual(result.data["duration"], 5)
        self.assertEqual(result.data["resolution"], "480p")
        saved = self.paths.resolve_under_agent(result.data["path"], must_exist=True)
        self.assertEqual(saved.read_bytes(), video)
        self.assertTrue(saved.is_relative_to(self.paths.workspace / "demo-project" / "artifacts" / "generated-videos"))
        post.assert_called_once_with(
            "https://api-us.0x567.com/v1/videos/generations",
            headers={"Authorization": "Bearer test-video-secret", "Content-Type": "application/json"},
            json={"model": "grok-imagine-video-1.5", "prompt": "A quiet ocean", "duration": 5, "resolution": "480p", "aspect_ratio": "16:9"},
            timeout=30.0,
        )
        self.assertEqual(get.call_args_list[0].args[0], "https://api-us.0x567.com/v1/videos/req-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.assertEqual(get.call_args_list[-1].args[0], "https://api-us.0x567.com/v1/videos/req-" + "x" * 300)
        self.assertNotIn("test-video-secret", str(result.to_dict()))

    def test_resume_request_id_does_not_submit_new_generation(self) -> None:
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post"
        ) as post, patch(
            "tools.builtin.generate_video.httpx.get",
            return_value=httpx.Response(200, json={"status": "processing"}),
        ) as get, patch("tools.builtin.generate_video.POLL_TIMEOUT_SECONDS", 0):
            result = run({"request_id": "req-resume"}, paths=self.paths)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "timeout")
        self.assertEqual(result.error.details["request_id"], "req-resume")
        post.assert_not_called()
        get.assert_called_once_with(
            "https://api-us.0x567.com/v1/videos/req-resume",
            headers={"Authorization": "Bearer test-secret"},
            timeout=30.0,
        )


    def test_resume_success_omits_unsubmitted_generation_parameters(self) -> None:
        video = b"\x00\x00\x00\x18ftypmp42video-fixture"
        @__import__("contextlib").contextmanager
        def stream_response(_method: str, _url: str, **_kwargs: object):
            yield httpx.Response(200, content=video)

        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.get",
            return_value=httpx.Response(200, json={"status": "done", "video": {"url": "https://cdn.0x567.com/v.mp4"}}),
        ), patch("tools.builtin.generate_video.httpx.stream", side_effect=stream_response):
            result = run({"request_id": "req-resumed-ok"}, paths=self.paths)

        self.assertTrue(result.ok)
        self.assertEqual(result.data["request_id"], "req-resumed-ok")
        self.assertNotIn("model", result.data)
        self.assertNotIn("duration", result.data)
        self.assertNotIn("resolution", result.data)
        self.assertNotIn("aspect_ratio", result.data)
        self.assertEqual(result.data["display_paths"], [result.data["path"]])

    def test_lite_model_is_rejected_before_submission(self) -> None:
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post",
            return_value=httpx.Response(200, json={"request_id": "req-lite"}),
        ) as post, patch(
            "tools.builtin.generate_video.httpx.get",
            return_value=httpx.Response(400),
        ):
            result = run({"prompt": "a film", "model": "grok-imagine-video-1.5-lite"}, paths=self.paths)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        post.assert_not_called()

    def test_original_video_model_is_accepted(self) -> None:
        from tools.builtin.generate_video import _validate_arguments

        self.assertEqual(_validate_arguments({"prompt": "a film", "model": "grok-imagine-video"})[1], "grok-imagine-video")

    def test_duration_above_provider_limit_is_rejected_before_submission(self) -> None:
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post"
        ) as post:
            result = run({"prompt": "a film", "duration": 16}, paths=self.paths)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        post.assert_not_called()

    def test_untrusted_video_url_is_rejected_without_download(self) -> None:
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post", return_value=httpx.Response(200, json={"request_id": "req-unsafe"})
        ), patch(
            "tools.builtin.generate_video.httpx.get",
            return_value=httpx.Response(200, json={"status": "done", "video": {"url": "https://evil.example/video.mp4"}}),
        ) as get:
            result = run({"prompt": "x"}, paths=self.paths)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        self.assertEqual(get.call_count, 1)


    def test_existing_destination_prevents_billable_submission(self) -> None:
        from tools.builtin.generate_video import _output_path

        target = _output_path(self.paths, project_root="workspace/demo-project", conversation_id="chat-1")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"existing")
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch(
            "tools.builtin.generate_video._output_path", return_value=target
        ), patch("tools.builtin.generate_video.httpx.post") as post:
            result = run({"prompt": "a film"}, paths=self.paths, project_root="workspace/demo-project")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        self.assertEqual(target.read_bytes(), b"existing")
        post.assert_not_called()

    def test_missing_key_and_invalid_prompt_fail_before_submit(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("tools.builtin.generate_video.httpx.post") as post:
            missing = run({"prompt": "x"}, paths=self.paths)
        self.assertEqual(missing.error.code, "missing_api_key")
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-secret"}), patch("tools.builtin.generate_video.httpx.post") as post:
            invalid = run({"prompt": "  "}, paths=self.paths)
        self.assertEqual(invalid.error.code, "validation_error")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()


