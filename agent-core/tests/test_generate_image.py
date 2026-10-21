"""Direct 0x567 image API runner tests; provider HTTP stays mocked."""

from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

import httpx
from host_scope import HostRoot, HostScopeConfig, save_host_scope, utc_now_iso
from image_references import ImageReference
from tools.builtin.generate_image import run, write_png_exclusive
from tests.isolation_helpers import make_temp_agent_paths


PNG_BYTES = b"\x89PNG\r\n\x1a\nvalid-png-payload"


class GenerateImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        (self.paths.workspace / "demo-project").mkdir(parents=True)

    def _success_response(self) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]},
        )

    def test_prompt_generation_posts_expected_json_and_writes_unique_png(self) -> None:
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post", return_value=self._success_response()
        ) as post:
            result = run(
                {"prompt": "A small blue house", "size": "1024x1024", "quality": "low"},
                paths=self.paths,
                project_root="workspace/demo-project",
                conversation_id="chat-1",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["display_paths"], [result.data["path"]])
        self.assertEqual(result.data["model"], "gpt-image-2.5-flare")
        self.assertFalse(result.data["used_reference_image"])
        self.assertEqual(result.data["bytes"], len(PNG_BYTES))
        saved = self.paths.resolve_under_agent(result.data["path"], must_exist=True)
        self.assertTrue(saved.is_relative_to(self.paths.workspace / "demo-project" / "artifacts" / "generated-images"))
        self.assertEqual(saved.read_bytes(), PNG_BYTES)
        post.assert_called_once()
        url = post.call_args.args[0]
        self.assertEqual(url, "https://api-cdn.0x567.com/v1/images/generations")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "model": "gpt-image-2.5-flare",
                "prompt": "A small blue house",
                "size": "1024x1024",
                "quality": "low",
                "n": 1,
            },
        )
        self.assertNotIn("test-image-secret", str(result.to_dict()))

    def test_reference_edit_posts_multipart_image_bytes(self) -> None:
        reference_path = self.paths.workspace / "reference.png"
        reference_path.write_bytes(PNG_BYTES)
        reference = ImageReference(
            reference_id="img-a1b2",
            path=reference_path,
            source_ref="workspace/reference.png",
            filename="reference.png",
            mime_type="image/png",
            session_id="old-chat",
            session_title="Old chat",
            updated_at="2026-10-20T00:00:00Z",
            context="reference context",
            message_index=2,
            attachment_index=0,
        )
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post", return_value=self._success_response()
        ) as post:
            result = run(
                {"prompt": "Make it a watercolor", "model": "gpt-image-2.5-sunburst"},
                paths=self.paths,
                prepared_reference=reference,
                project_root="workspace/demo-project",
                conversation_id="chat-1",
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.data["used_reference_image"])
        self.assertEqual(
            post.call_args.args[0],
            "https://api-cdn.0x567.com/v1/images/edits",
        )
        self.assertEqual(
            post.call_args.kwargs["data"],
            {
                "model": "gpt-image-2.5-sunburst",
                "prompt": "Make it a watercolor",
                "size": "1024x1024",
                "quality": "low",
                "n": "1",
            },
        )
        image_parts = post.call_args.kwargs["files"]
        self.assertEqual(len(image_parts), 1)
        self.assertEqual(image_parts[0][0], "image[]")
        self.assertEqual(image_parts[0][1], ("reference.png", PNG_BYTES, "image/png"))

    def test_saved_image_key_is_used_when_environment_key_is_unset(self) -> None:
        from llm_secrets import set_llm_secret

        set_llm_secret(self.paths, "LLM_0x567_KEY", "stored-image-test-secret")
        with patch.dict(os.environ, {"LLM_0x567_KEY": ""}), patch(
            "tools.builtin.generate_image.httpx.post", return_value=self._success_response()
        ) as post:
            result = run({"prompt": "a forest"}, paths=self.paths, conversation_id="chat-1")

        self.assertTrue(result.ok)
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer stored-image-test-secret",
        )
        self.assertNotIn("stored-image-test-secret", str(result.to_dict()))

    def test_read_authorized_host_reference_is_revalidated_and_sent(self) -> None:
        import tempfile

        host_root = Path(tempfile.mkdtemp(prefix="image-tool-host-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(host_root, ignore_errors=True))
        reference_path = host_root / "reference.png"
        reference_path.write_bytes(PNG_BYTES)
        save_host_scope(
            self.paths,
            HostScopeConfig(
                host_roots=[
                    HostRoot(
                        id="photos",
                        path=host_root,
                        label="Photos",
                        added_at=utc_now_iso(),
                        read=True,
                        write=False,
                    )
                ]
            ),
        )
        reference = ImageReference(
            reference_id="img-host",
            path=reference_path.resolve(),
            source_ref="host:photos/reference.png",
            filename="reference.png",
            mime_type="image/png",
            session_id="old-chat",
            session_title="Old chat",
            updated_at="2026-10-20T00:00:00Z",
            context="fixture",
            message_index=1,
            attachment_index=0,
        )
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post", return_value=self._success_response()
        ) as post:
            result = run(
                {"prompt": "use the local reference"},
                paths=self.paths,
                prepared_reference=reference,
                project_root="workspace/demo-project",
                conversation_id="chat-1",
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.data["used_reference_image"])
        self.assertEqual(post.call_args.args[0], "https://api-cdn.0x567.com/v1/images/edits")

    def test_missing_key_fails_without_calling_provider(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "tools.builtin.generate_image.httpx.post"
        ) as post:
            result = run({"prompt": "a forest"}, paths=self.paths, conversation_id="chat-1")

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "missing_api_key")
        post.assert_not_called()

    def test_empty_prompt_is_rejected_before_provider_call(self) -> None:
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post"
        ) as post:
            result = run({"prompt": "   "}, paths=self.paths)

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        post.assert_not_called()

    def test_invalid_size_is_rejected_before_provider_call(self) -> None:
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post"
        ) as post:
            result = run({"prompt": "a forest", "size": "13x17"}, paths=self.paths)

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        post.assert_not_called()

    def test_invalid_reference_signature_is_rejected_before_provider_call(self) -> None:
        reference_path = self.paths.workspace / "not-really.png"
        reference_path.write_bytes(b"not a png")
        reference = ImageReference(
            reference_id="img-bad",
            path=reference_path,
            source_ref="workspace/not-really.png",
            filename="not-really.png",
            mime_type="image/png",
            session_id="old-chat",
            session_title="Old chat",
            updated_at="2026-10-20T00:00:00Z",
            context="fixture",
            message_index=1,
            attachment_index=0,
        )
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post"
        ) as post:
            result = run(
                {"prompt": "use this"},
                paths=self.paths,
                prepared_reference=reference,
                conversation_id="chat-1",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        post.assert_not_called()

    def test_reference_at_size_limit_is_rejected_before_provider_call(self) -> None:
        from unittest.mock import patch as mock_patch

        reference_path = self.paths.workspace / "large.png"
        reference_path.write_bytes(PNG_BYTES)
        reference = ImageReference(
            reference_id="img-large",
            path=reference_path,
            source_ref="workspace/large.png",
            filename="large.png",
            mime_type="image/png",
            session_id="old-chat",
            session_title="Old chat",
            updated_at="2026-10-20T00:00:00Z",
            context="fixture",
            message_index=1,
            attachment_index=0,
        )
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), mock_patch(
            "tools.builtin.generate_image.MAX_REFERENCE_IMAGE_BYTES", len(PNG_BYTES)
        ), patch("tools.builtin.generate_image.httpx.post") as post:
            result = run(
                {"prompt": "use this"},
                paths=self.paths,
                prepared_reference=reference,
                conversation_id="chat-1",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        post.assert_not_called()

    def test_api_failure_and_malformed_image_are_not_reported_as_success(self) -> None:
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post",
            return_value=httpx.Response(429, json={"error": {"message": "rate limited"}}),
        ):
            failed = run({"prompt": "a forest"}, paths=self.paths, conversation_id="chat-1")
        self.assertFalse(failed.ok)
        self.assertEqual(failed.error.code, "http_error")

        malformed = httpx.Response(200, json={"data": [{"b64_json": "bm90LXBuZw=="}]})
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post", return_value=malformed
        ):
            bad_image = run({"prompt": "a forest"}, paths=self.paths, conversation_id="chat-1")
        self.assertFalse(bad_image.ok)
        self.assertEqual(bad_image.error.code, "validation_error")
        self.assertFalse((self.paths.workspace / "_drops" / "chat-1" / "generated-images").exists())

    def test_invalid_base64_response_is_rejected_without_creating_output(self) -> None:
        response = httpx.Response(200, json={"data": [{"b64_json": "%%%"}]})
        with patch.dict(os.environ, {"LLM_0x567_KEY": "test-image-secret"}), patch(
            "tools.builtin.generate_image.httpx.post", return_value=response
        ):
            result = run(
                {"prompt": "a forest"},
                paths=self.paths,
                conversation_id="bad-base64",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "validation_error")
        self.assertFalse(
            (self.paths.workspace / "_drops" / "bad-base64" / "generated-images").exists()
        )

    def test_exclusive_png_writer_never_overwrites(self) -> None:
        target = self.paths.workspace / "existing.png"
        target.write_bytes(b"original")

        with self.assertRaises(FileExistsError):
            write_png_exclusive(target, PNG_BYTES)

        self.assertEqual(target.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
