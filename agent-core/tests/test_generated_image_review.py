from __future__ import annotations

import sys
import base64
import binascii
import struct
import zlib
import unittest
from unittest.mock import patch
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import Agent, build_builtin_tool_definition
from llm_client import LLMResponse
from session import Session, SessionMeta, utc_now_iso
from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from paths import AgentPaths
from tools.schema import tool_ok
from tests.isolation_helpers import make_temp_agent_paths

def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)


PNG = (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
       + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x80\xff")) + _png_chunk(b"IEND", b""))
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


class GeneratedImageReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.output_dir = self.paths.workspace / "demo" / "artifacts" / "generated-images"
        self.output_dir.mkdir(parents=True)
        self.image_path = self.output_dir / "image-test.png"
        self.image_path.write_bytes(PNG)

    def test_png_fixture_has_valid_chunks_and_pixel_data(self) -> None:
        self.assertEqual(PNG[:8], b"\x89PNG\r\n\x1a\n")
        pos, chunks = 8, []
        while pos < len(PNG):
            length = struct.unpack(">I", PNG[pos:pos + 4])[0]
            kind = PNG[pos + 4:pos + 8]
            data = PNG[pos + 8:pos + 8 + length]
            crc = struct.unpack(">I", PNG[pos + 8 + length:pos + 12 + length])[0]
            self.assertEqual(crc, binascii.crc32(kind + data) & 0xffffffff)
            chunks.append((kind, data))
            pos += 12 + length
        self.assertEqual(pos, len(PNG))
        self.assertEqual([kind for kind, _ in chunks], [b"IHDR", b"IDAT", b"IEND"])
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[0][1]), (1, 1, 8, 2, 0, 0, 0))
        self.assertEqual(zlib.decompress(chunks[1][1]), b"\x00\x00\x80\xff")

    def test_success_result_image_bytes_become_an_image_url_payload(self) -> None:
        from agent import _generated_image_review_message

        ref = self.paths.to_agent_relative(self.image_path)
        result = tool_ok("generate_image", {"path": ref, "bytes": len(PNG)}, output_path=ref)
        message = _generated_image_review_message(result, self.paths, supports_image_input=True)
        self.assertIsNotNone(message)
        blocks = message["content"]
        self.assertTrue(any(block.get("type") == "image_url" for block in blocks))
        image = next(block for block in blocks if block.get("type") == "image_url")
        self.assertEqual(image["image_url"]["url"], PNG_URI)
        self.assertNotIn(str(self.image_path), str(message))

    def test_rejects_mismatched_tool_paths_and_non_output_paths(self) -> None:
        from agent import _generated_image_review_message

        output_ref = self.paths.to_agent_relative(self.image_path)
        other_path = self.paths.workspace / "other.png"
        other_path.write_bytes(PNG)
        result = tool_ok("generate_image", {"path": self.paths.to_agent_relative(other_path)}, output_path=output_ref)
        message = _generated_image_review_message(result, self.paths, supports_image_input=True)
        self.assertIsNone(message)

        result = tool_ok("generate_image", {"path": self.paths.to_agent_relative(other_path)}, output_path=self.paths.to_agent_relative(other_path))
        self.assertIsNone(_generated_image_review_message(result, self.paths, supports_image_input=True))

    def test_reference_selection_tool_ok_is_not_treated_as_generated_image(self) -> None:
        from agent import _generated_image_review_message

        selection = tool_ok("generate_image", {
            "status": "reference_selection_required",
            "candidates": [{"id": "candidate-1"}],
        })
        self.assertIsNone(_generated_image_review_message(selection, self.paths, supports_image_input=True))

    def test_non_vision_model_reports_not_viewed(self) -> None:
        from agent import _generated_image_review_message

        ref = self.paths.to_agent_relative(self.image_path)
        result = tool_ok("generate_image", {"path": ref, "bytes": len(PNG)}, output_path=ref)
        message = _generated_image_review_message(result, self.paths, supports_image_input=False)
        self.assertIsNone(message)

    def test_simulated_model_receives_generated_png_bytes_in_next_same_turn_call(self) -> None:
        with temporary_agent_paths() as paths:
            session = Session(
                conversation_id="generated-image-review",
                session_dir=paths.data / "sessions" / "generated-image-review",
                goal="generate an image",
                meta=SessionMeta(topics=[], llm_model="0x567-pro", updated_at=utc_now_iso(), phase="S4", turn_mode="agent"),
                messages=[], paths=paths,
            )
            session.turn_intent = "execute"
            generated = paths.workspace / "demo" / "artifacts" / "generated-images" / "image-test.png"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(PNG)
            ref = paths.to_agent_relative(generated)
            class SimulatedLLM:
                def __init__(self) -> None:
                    self.calls = []
                def set_cancel_event(self, _event) -> None:
                    pass
                def chat(self, messages, **_kwargs):
                    self.calls.append(messages)
                    if len(self.calls) == 1:
                        return LLMResponse(model="0x567-pro", content=None, tool_calls=[{
                            "id": "gen-1", "type": "function", "function": {
                                "name": "generate_image", "arguments": '{"prompt":"a blue bird"}'
                            }
                        }], finish_reason="tool_calls", usage=None, raw={})
                    return LLMResponse(model="0x567-pro", content="I can see a blue bird with a yellow beak.", tool_calls=[], finish_reason="stop", usage=None, raw={})
            llm = SimulatedLLM()
            executor = ToolExecutor(registry=ToolRegistry.load(paths), session=ExecutorSession(session_dir=session.session_dir), confirm_fn=lambda _req: True)
            from tools.schema import tool_ok
            executor.run = lambda _name, _args: tool_ok("generate_image", {"path": ref, "bytes": len(PNG)}, output_path=ref)
            agent = Agent(session=session, executor=executor, llm=llm)
            with patch("turn_intent.should_spawn_explore_for_turn", return_value=False):
                result = agent.run_turn("Please generate a blue bird")
            self.assertEqual(result.assistant_text, "I can see a blue bird with a yellow beak.")
            self.assertEqual(len(llm.calls), 2)
            multimodal = [m for m in llm.calls[1] if m.get("role") == "user" and isinstance(m.get("content"), list)
                          and any(isinstance(block, dict) and block.get("type") == "image_url" for block in m["content"])]
            self.assertEqual(len(multimodal), 1)
            image_block = next(block for block in multimodal[0]["content"] if block.get("type") == "image_url")
            self.assertEqual(image_block["image_url"]["url"], PNG_URI)

    def _run_review_scenario(self, *, max_rounds, cancel_after_generation=False, tool_only=False):
        paths = self.paths
        session = Session(
            conversation_id="review-edge", session_dir=paths.data / "sessions" / "review-edge",
            goal="generate image", meta=SessionMeta(topics=[], llm_model="0x567-pro",
                updated_at=utc_now_iso(), phase="S4", turn_mode="agent"), messages=[], paths=paths,
        )
        session.turn_intent = "execute"
        session.append_message({"role": "user", "content": "Generate a picture"})
        ref = paths.to_agent_relative(self.image_path)
        calls = []
        def response(content=None, tool_calls=None):
            return LLMResponse(model="0x567-pro", content=content, tool_calls=tool_calls or [],
                               finish_reason="tool_calls" if tool_calls else "stop", usage=None, raw={})
        def call(name, ident):
            return {"id": ident, "type": "function", "function": {"name": name, "arguments": "{}"}}
        class Model:
            def set_cancel_event(self, event):
                self.cancel = event
            def chat(self, messages, **kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    return response(tool_calls=[call("generate_image", "one")])
                if len(calls) == 2 and tool_only:
                    return response(tool_calls=[call("list_dir", "two")])
                return response(content="done")
        executor = ToolExecutor(registry=ToolRegistry.load(paths), session=ExecutorSession(session_dir=session.session_dir), confirm_fn=lambda *_: True)
        agent = Agent(session=session, executor=executor, llm=Model())
        notices = []
        agent.on_turn_event = notices.append
        def run(name, args):
            if name == "generate_image":
                if cancel_after_generation:
                    agent.cancel_event.set()
                return tool_ok(name, {"path": ref, "bytes": len(PNG)}, output_path=ref)
            return tool_ok(name, {"entries": []})
        executor.run = run
        result = agent._run_parent_tool_loop(max_rounds=max_rounds, tools=[build_builtin_tool_definition("generate_image")],
                                              model="0x567-pro", segment_start_index=0)
        return result, calls, notices

    def test_last_tool_round_warns_generated_image_was_not_reviewed(self):
        result, calls, notices = self._run_review_scenario(max_rounds=1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result.exceeded)
        self.assertIn("\u672a\u67e5\u770b", result.final_text)
        self.assertTrue(any("\u672a\u67e5\u770b" in str(event.get("text", "")) for event in notices))

    def test_cancel_immediately_after_generation_warns_not_reviewed(self):
        result, calls, notices = self._run_review_scenario(max_rounds=3, cancel_after_generation=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.finish_reason, "cancelled")
        self.assertIn("\u672a\u67e5\u770b", result.final_text)
        self.assertTrue(any("\u672a\u67e5\u770b" in str(event.get("text", "")) for event in notices))

    def test_vision_tool_call_only_does_not_claim_review_and_does_not_resend(self):
        result, calls, notices = self._run_review_scenario(max_rounds=3, tool_only=True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum(str(messages).count(PNG_URI) for messages in calls), 1)
        self.assertTrue(any("\u672a\u5b8c\u6210" in str(event.get("text", "")) for event in notices))
        self.assertTrue(any("\u672a\u5b8c\u6210" in str(message.get("content", "")) for message in calls[2]))

    def test_schema_and_tool_guidance_preserve_explicit_reference_path(self) -> None:
        definition = build_builtin_tool_definition("generate_image")
        properties = definition["function"]["parameters"]["properties"]
        self.assertIn("reference_image_path", properties)
        guidance = definition["function"]["description"]
        self.assertIn("reference_image_path", guidance)
        self.assertIn("exact", guidance.casefold())
        self.assertIn("guess", guidance.casefold())


if __name__ == "__main__":
    unittest.main()




