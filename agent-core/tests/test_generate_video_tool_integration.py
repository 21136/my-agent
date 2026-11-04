"""Focused integration coverage for builtin generate_video dispatch and safety."""
from __future__ import annotations

import base64
import contextlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import build_builtin_tool_definition, build_llm_tools
from loader import format_builtin_summary, load_tool_catalog_index
from paths import AgentPaths
from session import _is_chat_image_rel_path, create_new
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import tool_ok
from turn_intent import is_explicit_video_generation_request, resolve_ordinary_work_intent
from tests.isolation_helpers import make_temp_agent_paths


class GenerateVideoToolIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        (self.paths.workspace / "demo-project").mkdir(parents=True)
        self.registry = ToolRegistry.load(self.paths)

    def _session(self, cid: str, mode: str = "agent", intent: str = "execute"):
        session = create_new(self.paths, conversation_id=cid)
        session.set_goal("video"); session.persist_goal()
        session.meta.turn_mode = mode
        session._ordinary_work_intent = intent
        return session

    def _executor(self, session, confirm):
        return ToolExecutor(
            registry=self.registry,
            session=ExecutorSession(
                session_dir=session.session_dir, project_root="workspace/demo-project",
                project_id="demo-project", turn_mode=session.meta.turn_mode,
                ordinary_work_intent=session._ordinary_work_intent, active_shell="project",
            ),
            confirm_fn=confirm,
        )

    def test_registered_schema_is_available_in_ordinary_and_project_agent_sessions(self):
        entry = self.registry.get_builtin("generate_video")
        self.assertIsNotNone(entry)
        self.assertTrue(entry.confirm)
        params = build_builtin_tool_definition("generate_video")["function"]["parameters"]
        self.assertIn({"required": ["prompt"]}, params["anyOf"])
        self.assertIn({"required": ["request_id"]}, params["anyOf"])
        self.assertEqual(params["properties"]["model"]["default"], "grok-imagine-video-1.5")
        self.assertEqual(params["properties"]["model"]["enum"], ["grok-imagine-video-1.5", "grok-imagine-video"])
        self.assertEqual(params["properties"]["duration"]["default"], 5)
        self.assertEqual(params["properties"]["resolution"]["default"], "480p")
        self.assertEqual(params["properties"]["aspect_ratio"]["default"], "16:9")
        self.assertEqual(params["properties"]["request_id"]["maxLength"], 1024)
        self.assertIn("request_id", params["properties"])
        ordinary = self._session("ordinary-video")
        project = self._session("project-video")
        for session in (ordinary, project):
            names = {t["function"]["name"] for t in build_llm_tools(session, registry=self.registry)}
            self.assertIn("generate_video", names)
        self.assertNotIn("generate_video", {t["function"]["name"] for t in build_llm_tools(self._session("ask-video", "ask"), registry=self.registry)})
        index = load_tool_catalog_index(AgentPaths.discover(Path(__file__).resolve()))
        summary = format_builtin_summary()
        self.assertIn("generate_video", index)
        self.assertIn("generate_video", summary)

    def test_ask_read_only_and_subagent_contexts_reject_before_confirmation(self):
        for cid, mode, intent, blocked in (
            ("ask-video", "ask", "execute", frozenset()),
            ("readonly-video", "agent", "read_only", frozenset()),
            ("plan-video", "agent", "plan_only", frozenset()),
            ("subagent-video", "agent", "execute", frozenset({"generate_video"})),
        ):
            session = self._session(cid, mode, intent)
            executor = self._executor(session, lambda *_: self.fail("must not confirm"))
            executor.session.blocked_tools = blocked
            result = executor.validate("generate_video", {"prompt": "a short clip"})
            self.assertIsNotNone(result, cid)
            self.assertFalse(result.ok, cid)

    def test_confirmed_dispatch_passes_worker_interface_and_defaults_without_provider_call(self):
        session = self._session("run-video")
        previews = []
        executor = self._executor(session, lambda text, _all: previews.append(text) or "y")
        result = tool_ok("generate_video", {"request_id": "job-1"})
        with patch("tools.executor._BUILTIN_RUNNERS", {**__import__("tools.executor", fromlist=["_BUILTIN_RUNNERS"])._BUILTIN_RUNNERS,
                                                       "generate_video": lambda args, **kw: seen.append((args, kw)) or result}):
            seen = []
            actual = executor.run("generate_video", {"prompt": "a red kite over water"})
        self.assertTrue(actual.ok)
        args, kwargs = seen[0]
        self.assertEqual(args, {"prompt": "a red kite over water", "model": "grok-imagine-video-1.5"})
        self.assertIn("Model: grok-imagine-video-1.5", previews[0])
        self.assertEqual(kwargs, {
            "paths": self.paths,
            "project_root": "",
            "conversation_id": session.session_dir.name,
            "prepared_reference": None,
            "reference_image_path": None,
        })
        explicit_seen = []
        import tools.executor as executor_module
        with patch.dict(executor_module._BUILTIN_RUNNERS, {"generate_video": lambda args, **kw: explicit_seen.append((args, kw)) or result}):
            self.assertTrue(executor.run("generate_video", {"prompt": "another clip", "model": "grok-imagine-video"}).ok)
        self.assertEqual(explicit_seen[0][0]["model"], "grok-imagine-video")
        self.assertIn("Model: grok-imagine-video", previews[1])
        self.assertIsNotNone(executor.validate("generate_video", {"prompt": "a clip", "model": "grok-imagine-video-1.5-lite"}))
        project_session = self._session("run-project-video")
        project_session.meta.project_id = "demo-project"
        project_session.meta.project_root = "workspace/demo-project"
        project_session.meta.active_shell = "project"
        project_session.save()
        project_executor = self._executor(project_session, lambda *_: "y")
        project_seen = []
        import tools.executor as executor_module
        with patch.dict(executor_module._BUILTIN_RUNNERS, {"generate_video": lambda args, **kw: project_seen.append((args, kw)) or result}):
            self.assertTrue(project_executor.run("generate_video", {"prompt": "another clip"}).ok)
        self.assertEqual(project_seen[0][1]["project_root"], "workspace/demo-project")
        self.assertIn("credit", previews[0].lower())

    def test_tool_executor_confirmed_image_to_video_posts_png_and_saves_mocked_mp4(self):
        session = self._session("e2e-video-reference")
        image = self.paths.workspace / "demo-project" / "approved-reference.png"
        image_bytes = bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"offline-png-fixture"
        image.write_bytes(image_bytes)
        previews = []
        executor = self._executor(session, lambda text, _all: previews.append(text) or "y")
        mp4_bytes = b"\x00\x00\x00\x18ftypmp42offline-video-fixture"

        @contextlib.contextmanager
        def fake_stream(_method, _url, **_kwargs):
            yield httpx.Response(200, content=mp4_bytes)

        arguments = {
            "prompt": "Animate the uploaded reference gently",
            "model": "grok-imagine-video",
            "duration": 4,
            "resolution": "720p",
            "aspect_ratio": "16:9",
            "reference_image_path": "workspace/demo-project/approved-reference.png",
        }
        expected_json = {
            "model": "grok-imagine-video",
            "prompt": "Animate the uploaded reference gently",
            "duration": 4,
            "resolution": "720p",
            "aspect_ratio": "16:9",
            "image": {"url": "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")},
        }
        with patch.dict(os.environ, {"LLM_0x567_KEY": "offline-test-secret"}), patch(
            "tools.builtin.generate_video.httpx.post",
            return_value=httpx.Response(200, json={"request_id": "offline-job-1"}),
        ) as post, patch(
            "tools.builtin.generate_video.httpx.get",
            return_value=httpx.Response(200, json={"status": "done", "video": {"url": "https://cdn.0x567.com/offline.mp4"}}),
        ) as poll, patch(
            "tools.builtin.generate_video.httpx.stream", side_effect=fake_stream,
        ) as download:
            result = executor.run("generate_video", arguments)

        self.assertTrue(result.ok, result)
        self.assertEqual(len(previews), 1)
        self.assertIn("explicit local image path", previews[0].lower())
        self.assertIn(str(image.resolve()), previews[0])
        self.assertIn("only after approval", previews[0])
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://api-us.0x567.com/v1/videos/generations")
        self.assertEqual(post.call_args.kwargs["json"], expected_json)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer offline-test-secret")
        self.assertEqual(poll.call_count, 1)
        download.assert_called_once()
        output = self.paths.resolve_under_agent(result.data["path"], must_exist=True)
        self.assertEqual(output.read_bytes(), mp4_bytes)
        self.assertEqual(result.data["mime_type"], "video/mp4")
        self.assertNotIn("offline-test-secret", str(result.to_dict()))

    def test_paid_generation_never_uses_ordinary_confirm_budget_and_accepts_long_request_id(self):
        session = self._session("budget-video")
        executor = self._executor(session, lambda *_: "y")
        executor.session.active_shell = "project"
        executor.session.ordinary_visible_confirms_this_turn = 1
        executor.session.project_root = "workspace/demo-project"
        request_id = "r" * 1024
        self.assertIsNone(executor.validate("generate_video", {"request_id": request_id}))
        self.assertEqual(executor.validate("generate_video", {"request_id": request_id + "x"}).error.code, "validation_error")
        seen = []
        confirmations = []
        executor.confirm_fn = lambda preview, _all: confirmations.append(preview) or "y"
        mock_result = tool_ok("generate_video", {"request_id": request_id})
        import tools.executor as executor_module
        with patch.dict(executor_module._BUILTIN_RUNNERS, {"generate_video": lambda args, **kwargs: seen.append((args, kwargs)) or mock_result}):
            result = executor.run("generate_video", {"request_id": request_id})
        self.assertTrue(result.ok)
        self.assertEqual(seen[0][0], {"request_id": request_id})
        self.assertEqual(len(confirmations), 1)
        self.assertIn(request_id[:80], confirmations[0]) if request_id[:80] in confirmations[0] else self.assertIn("request_id", confirmations[0])

    def test_plan_only_run_is_denied_before_confirmation_and_dispatch(self):
        session = self._session("plan-only-run", intent="plan_only")
        executor = self._executor(session, lambda *_: self.fail("plan-only must not confirm"))
        import tools.executor as executor_module
        runner = Mock()
        with patch.dict(executor_module._BUILTIN_RUNNERS, {"generate_video": runner}):
            result = executor.run("generate_video", {"prompt": "a short clip"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "permission_denied")
        runner.assert_not_called()

    def test_rejected_confirmation_never_dispatches(self):
        session = self._session("reject-video")
        executor = self._executor(session, lambda *_: "n")
        with patch("tools.executor._BUILTIN_RUNNERS") as runners:
            result = executor.run("generate_video", {"prompt": "a short clip"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "confirm_rejected")
        runners.__getitem__.assert_not_called()

    def test_explicit_request_detection_and_intent_resolution(self):
        self.assertTrue(is_explicit_video_generation_request("\u751f\u6210\u4e00\u4e2a 5 \u79d2\u89c6\u9891"))
        self.assertTrue(is_explicit_video_generation_request("generate a video of a red kite"))
        self.assertTrue(is_explicit_video_generation_request("Can you generate a video of a red kite?"))
        self.assertTrue(is_explicit_video_generation_request("\u80fd\u4e0d\u80fd\u751f\u6210\u4e00\u4e2a\u89c6\u9891\u5c0f\u732b\u5728\u98de\uff1f"))
        self.assertFalse(is_explicit_video_generation_request("Can you generate a video?"))
        self.assertFalse(is_explicit_video_generation_request("\u80fd\u4e0d\u80fd\u751f\u6210\u4e00\u4e2a\u89c6\u9891\uff1f"))
        self.assertFalse(is_explicit_video_generation_request("\u600e\u4e48\u5b9e\u73b0\u89c6\u9891\u751f\u6210\uff1f"))
        self.assertFalse(is_explicit_video_generation_request("\u5982\u4f55\u751f\u6210\u89c6\u9891\uff1f"))
        self.assertFalse(is_explicit_video_generation_request("\u4e0d\u8981\u751f\u6210\u89c6\u9891"))
        self.assertEqual(resolve_ordinary_work_intent("\u751f\u6210\u4e00\u4e2a 5 \u79d2\u89c6\u9891", None), "execute")
        self.assertEqual(resolve_ordinary_work_intent("\u53ea\u8bfb\uff1a\u5982\u4f55\u751f\u6210\u89c6\u9891", None), "read_only")
        self.assertFalse(_is_chat_image_rel_path("workspace/_drops/demo/movie.mp4"))


if __name__ == "__main__":
    unittest.main()
