from __future__ import annotations

import json
import secrets
import sys
import unittest
from pathlib import Path
from typing import Any

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from activity_router import compute_activity_route, compute_session_route
from agent import Agent
from evolve import build_checkpoint_messages
from llm_client import DEFAULT_MODEL, DEFAULT_MODEL_CODING, LLMResponse, resolve_session_model
from loader import build_system_prompt
from session import Session, SessionMeta, build_anchor_message, create_new, utc_now_iso
from tests.isolation_helpers import make_temp_agent_paths


class LegacySessionTopicsAreInertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_model_default_ignores_topics_and_set_topics_preserves_preference(self) -> None:
        self.assertEqual(resolve_session_model(["coding"]), DEFAULT_MODEL)
        session = create_new(self.paths, conversation_id=f"_topics-{secrets.token_hex(3)}")
        existing_model = session.meta.llm_model
        session.set_topics(["coding"])
        self.assertEqual(session.meta.llm_model, existing_model)

        session.set_llm_model("pro")
        session.set_topics(["workflow", "coding"])
        self.assertTrue(session.meta.llm_model_override)
        self.assertEqual(session.meta.llm_model, DEFAULT_MODEL_CODING)

    def test_legacy_topics_load_but_do_not_restore_implicit_pro_model(self) -> None:
        session = create_new(self.paths, conversation_id=f"_legacy-{secrets.token_hex(3)}")
        payload = session.meta.to_dict()
        payload.update({"topics": ["coding"], "llm_model": "0x567-pro", "llm_model_override": False})
        session.meta_path.write_text(json.dumps(payload), encoding="utf-8")

        loaded = Session.load(self.paths, session.conversation_id)
        self.assertEqual(loaded.meta.topics, ["coding"])
        self.assertEqual(loaded.meta.llm_model, DEFAULT_MODEL)
        self.assertFalse(loaded.meta.llm_model_override)

    def test_legacy_topics_do_not_change_route_for_same_message(self) -> None:
        session = Session(
            conversation_id="_route-topics",
            session_dir=self.paths.data / "sessions" / "_route-topics",
            goal="ordinary task",
            meta=SessionMeta(topics=["coding"], llm_model=DEFAULT_MODEL, updated_at=utc_now_iso()),
            messages=[],
            paths=self.paths,
        )
        without_topics = Session(
            conversation_id=session.conversation_id,
            session_dir=session.session_dir,
            goal=session.goal,
            meta=SessionMeta(topics=[], llm_model=DEFAULT_MODEL, updated_at=utc_now_iso()),
            messages=[],
            paths=self.paths,
        )
        with_legacy_topics = compute_activity_route(
            user_text="继续这个普通任务",
            intent="execute",
            session=session,
            paths=self.paths,
        )
        without_legacy_topics = compute_activity_route(
            user_text="继续这个普通任务",
            intent="execute",
            session=without_topics,
            paths=self.paths,
        )
        self.assertEqual(with_legacy_topics, without_legacy_topics)
        self.assertEqual(with_legacy_topics.shell, "daily")

        session.meta.active_shell = "daily"
        without_topics.meta.active_shell = "daily"
        self.assertEqual(
            compute_session_route(session, self.paths),
            compute_session_route(without_topics, self.paths),
        )

    def test_legacy_topics_do_not_change_evolution_prompt_or_default_asset_category(self) -> None:
        session = Session(
            conversation_id="_evolve-topics",
            session_dir=self.paths.data / "sessions" / "_evolve-topics",
            goal="ordinary task",
            meta=SessionMeta(topics=["coding"], llm_model=DEFAULT_MODEL, updated_at=utc_now_iso()),
            messages=[{"role": "user", "content": "记住这条规则"}],
            paths=self.paths,
        )
        without_topics = Session(
            conversation_id=session.conversation_id,
            session_dir=session.session_dir,
            goal=session.goal,
            meta=SessionMeta(topics=[], llm_model=DEFAULT_MODEL, updated_at=utc_now_iso()),
            messages=session.messages,
            paths=self.paths,
        )
        self.assertEqual(
            build_checkpoint_messages(session, trigger_phrase="记住", user_line="记住这条规则", paths=self.paths),
            build_checkpoint_messages(without_topics, trigger_phrase="记住", user_line="记住这条规则", paths=self.paths),
        )

    def test_real_agent_backend_chat_code_chat_uses_request_scoped_prompt(self) -> None:
        skill_dir = self.paths.evolve / "skills" / "systematic-debugging"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: systematic-debugging\ndescription: Debug code failures.\n"
            "triggers: [调试]\nallowed_tools: [read_file]\nrisk_level: low\n"
            "verification: regression test\nversion: 1.0.0\n---\n\nDEBUG-SKILL-SENTINEL",
            encoding="utf-8",
        )
        (self.paths.evolve / "prompts").mkdir(parents=True, exist_ok=True)
        (self.paths.evolve / "prompts" / "coding.md").write_text(
            "LEGACY-CODING-CONSTRAINT-SENTINEL", encoding="utf-8"
        )
        (self.paths.evolve / "_index.core.toml").write_text(
            '[[topic]]\nid = "coding"\nname = "Coding"\ndescription = "legacy"\n'
            'prompt = "prompts/coding.md"\nmemory_dirs = []\ntool_dirs = []\n',
            encoding="utf-8",
        )
        session = create_new(self.paths, conversation_id=f"_turns-{secrets.token_hex(3)}")
        metadata = session.meta.to_dict()
        metadata.update(
            {"topics": ["coding"], "llm_model": DEFAULT_MODEL_CODING, "llm_model_override": True}
        )
        session.meta_path.write_text(json.dumps(metadata), encoding="utf-8")
        session = Session.load(self.paths, session.conversation_id)

        class IsolatedMockModel:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            def set_cancel_event(self, _event: Any) -> None:
                pass

            def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
                self.prompts.append(
                    "\n".join(
                        str(message.get("content") or "")
                        for message in messages
                        if message.get("role") == "system"
                    )
                )
                return LLMResponse(
                    model=DEFAULT_MODEL_CODING,
                    content="mock response",
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    raw={},
                )

        model = IsolatedMockModel()
        agent = Agent.create(session, llm=model, confirm_fn=lambda _preview, _allow: "y")
        for text in ("你好，今天怎么样？", "程序报错了，请帮我调试。", "谢谢，先聊到这里。"):
            agent.run_turn(text, spawn_explore=False)

        self.assertEqual(len(model.prompts), 3)
        self.assertEqual(session.meta.llm_model, DEFAULT_MODEL_CODING)
        self.assertTrue(session.meta.llm_model_override)
        for prompt in model.prompts:
            self.assertNotIn("LEGACY-CODING-CONSTRAINT-SENTINEL", prompt)
            self.assertNotIn("topic_prompt:coding", prompt)
            self.assertNotIn("主题: coding", prompt)
        self.assertNotIn("skill_context", model.prompts[0])
        self.assertIn("DEBUG-SKILL-SENTINEL", model.prompts[1])
        self.assertNotIn("skill_context", model.prompts[2])

    def test_topics_do_not_enter_current_system_prompt_or_anchor(self) -> None:
        evolve = self.paths.evolve
        (evolve / "prompts").mkdir(parents=True, exist_ok=True)
        (evolve / "prompts" / "coding.md").write_text("LEGACY-CODING-PROMPT", encoding="utf-8")
        (evolve / "_index.core.toml").write_text(
            '[[topic]]\nid = "coding"\nname = "Coding"\ndescription = "legacy"\n'
            'prompt = "prompts/coding.md"\nmemory_dirs = []\ntool_dirs = []\n',
            encoding="utf-8",
        )
        session = Session(
            conversation_id="_prompt-topics",
            session_dir=self.paths.data / "sessions" / "_prompt-topics",
            goal="ordinary chat",
            meta=SessionMeta(topics=["coding"], llm_model=DEFAULT_MODEL, updated_at=utc_now_iso()),
            messages=[],
            paths=self.paths,
        )
        prompt = build_system_prompt(session, paths=self.paths).prompt
        self.assertNotIn("LEGACY-CODING-PROMPT", prompt)
        self.assertNotIn("已加载主题 prompt", prompt)
        self.assertNotIn("topics: coding", prompt)
        self.assertNotIn("主题: coding", build_anchor_message(session)["content"])


if __name__ == "__main__":
    unittest.main()
