"""Local full-text search over past sessions (dialogue_index)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from agent import BUILTIN_TOOL_NAMES, build_builtin_tool_definition
from dialogue_index import index_path, search_dialogue
from paths import AgentPaths
from session import Session, create_new
from subagent import EXPLORE_TOOL_NAMES
from tools.executor import ExecutorSession, ToolExecutor
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode

from tests.isolation_helpers import make_temp_agent_paths


class DialogueIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths: AgentPaths = make_temp_agent_paths(self)

    def _session(self, *messages: tuple[str, str]) -> Session:
        session = create_new(self.paths)
        for role, content in messages:
            session.append_message({"role": role, "content": content})
        return session

    def test_finds_chinese_substring_in_another_session(self) -> None:
        old = self._session(("user", "上次我们把压缩阈值定成十五万了"), ("assistant", "好的，已经记下。"))
        self._session(("user", "今天聊点别的"))

        hits = search_dialogue(self.paths, "压缩阈值")

        self.assertEqual([(hit.session_id, hit.role) for hit in hits], [(old.conversation_id, "user")])
        self.assertIn("压缩阈值定成十五万", hits[0].snippet)
        self.assertEqual(hits[0].message_index, 0)

    def test_two_character_term_and_multiple_terms_must_all_match(self) -> None:
        both = self._session(("assistant", "部署脚本在 deploy.ps1，端口 8765"))
        self._session(("assistant", "部署前先跑测试"))

        self.assertEqual(len(search_dialogue(self.paths, "部署")), 2)
        hits = search_dialogue(self.paths, "部署 8765")
        self.assertEqual([hit.session_id for hit in hits], [both.conversation_id])

    def test_tool_output_and_host_anchor_rows_are_not_indexed(self) -> None:
        session = self._session(("user", "[本次会议上下文]\nSECRETANCHOR 目标"), ("user", "帮我看下日志"))
        session.append_message({"role": "tool", "tool_call_id": "c1", "content": "TOOLOUTPUTMARKER 一大段输出"})
        session.append_message({"role": "assistant", "content": None, "tool_calls": []})

        self.assertEqual(search_dialogue(self.paths, "TOOLOUTPUTMARKER"), [])
        self.assertEqual(search_dialogue(self.paths, "SECRETANCHOR"), [])
        self.assertEqual(len(search_dialogue(self.paths, "看下日志")), 1)

    def test_current_session_can_be_excluded(self) -> None:
        current = self._session(("user", "关于迁移方案的讨论"))
        other = self._session(("user", "迁移方案上周定了"))

        hits = search_dialogue(self.paths, "迁移方案", exclude_session_id=current.conversation_id)

        self.assertEqual([hit.session_id for hit in hits], [other.conversation_id])

    def test_index_follows_new_changed_and_deleted_sessions(self) -> None:
        session = self._session(("user", "第一版说法 ALPHAWORD"))
        self.assertEqual(len(search_dialogue(self.paths, "ALPHAWORD")), 1)

        session.append_message({"role": "assistant", "content": "补充 BETAWORD"})
        self.assertEqual(len(search_dialogue(self.paths, "BETAWORD")), 1)

        session.messages[0] = {"role": "user", "content": "改写后的说法 GAMMAWORD"}
        session.save()
        self.assertEqual(search_dialogue(self.paths, "ALPHAWORD"), [])
        self.assertEqual(len(search_dialogue(self.paths, "GAMMAWORD")), 1)

        session.messages_path.unlink()
        self.assertEqual(search_dialogue(self.paths, "GAMMAWORD"), [])

    def test_query_syntax_characters_are_literal(self) -> None:
        self._session(("user", 'he said "50%_off" AND left'))

        self.assertEqual(len(search_dialogue(self.paths, '"50%_off"')), 1)
        self.assertEqual(len(search_dialogue(self.paths, "%_")), 1)
        self.assertEqual(search_dialogue(self.paths, "5_%"), [])
        self.assertEqual(search_dialogue(self.paths, "   "), [])

    def test_limit_is_capped_and_snippet_is_bounded(self) -> None:
        for number in range(25):
            self._session(("user", "前缀" * 400 + f" NEEDLEWORD {number} " + "后缀" * 400))

        hits = search_dialogue(self.paths, "NEEDLEWORD", limit=500)

        self.assertEqual(len(hits), 20)
        self.assertLess(len(hits[0].snippet), 300)
        self.assertIn("NEEDLEWORD", hits[0].snippet)
        self.assertTrue(hits[0].snippet.startswith("…") and hits[0].snippet.endswith("…"))

    def test_index_lives_under_data(self) -> None:
        self._session(("user", "anything at all"))
        search_dialogue(self.paths, "anything")
        self.assertTrue(index_path(self.paths).is_file())
        self.assertEqual(index_path(self.paths).parent.parent, self.paths.data)


class SearchPastDialogueToolTests(unittest.TestCase):
    """The builtin never searches without the user's say-so, in any turn mode."""

    def setUp(self) -> None:
        self.paths: AgentPaths = make_temp_agent_paths(self)
        self.other = create_new(self.paths)
        self.other.append_message({"role": "user", "content": "发布窗口定在周四晚上"})
        self.current = create_new(self.paths)
        self.current.append_message({"role": "user", "content": "发布窗口是哪天来着"})
        self.previews: list[str] = []

    def _executor(self, answer: str) -> ToolExecutor:
        def confirm_fn(preview: str, _allow_approve_all: bool) -> str:
            self.previews.append(preview)
            return answer

        return ToolExecutor(
            registry=ToolRegistry.load(self.paths),
            session=ExecutorSession.load(self.current.session_dir),
            confirm_fn=confirm_fn,
        )

    def test_rejected_confirm_returns_nothing_and_builds_no_index(self) -> None:
        result = self._executor("n").run("search_past_dialogue", {"query": "发布窗口"})

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.CONFIRM_REJECTED)
        self.assertEqual(len(self.previews), 1)
        self.assertIn("发布窗口", self.previews[0])
        self.assertIn("发给模型", self.previews[0])
        self.assertFalse(index_path(self.paths).exists())

    def test_confirmed_search_returns_other_sessions_only(self) -> None:
        result = self._executor("y").run("search_past_dialogue", {"query": "发布窗口"})

        self.assertTrue(result.ok, result.error)
        hits = result.data["hits"]
        self.assertEqual([hit["session_id"] for hit in hits], [self.other.conversation_id])
        self.assertIn("周四晚上", hits[0]["snippet"])

    def test_every_call_asks_again(self) -> None:
        executor = self._executor("y")
        executor.run("search_past_dialogue", {"query": "发布窗口"})
        executor.run("search_past_dialogue", {"query": "周四"})

        self.assertEqual(len(self.previews), 2)

    def test_invalid_arguments_fail_validation(self) -> None:
        executor = self._executor("y")
        for arguments in ({}, {"query": "  "}, {"query": "x" * 201}, {"query": "ok", "limit": 0}):
            with self.subTest(arguments=arguments):
                result = executor.run("search_past_dialogue", arguments)
                self.assertFalse(result.ok)
                self.assertEqual(result.error.code, ToolErrorCode.VALIDATION_ERROR)

    def test_tool_is_offered_to_the_model_with_matching_schema(self) -> None:
        definition = build_builtin_tool_definition("search_past_dialogue")["function"]

        self.assertEqual(definition["parameters"]["required"], ["query"])
        self.assertIn("search_past_dialogue", BUILTIN_TOOL_NAMES)
        self.assertNotIn("search_past_dialogue", EXPLORE_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
