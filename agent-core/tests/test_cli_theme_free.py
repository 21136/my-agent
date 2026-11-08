from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from main import ConversationRepl
from paths import AgentPaths
from session import create_new


class CliThemeFreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "workspace").mkdir()
        evolve = root / "evolve"
        evolve.mkdir()
        (evolve / "_index.core.toml").write_text("", encoding="utf-8")
        (evolve / "_index.toml").write_text("", encoding="utf-8")
        self.paths = AgentPaths.from_root(root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_legacy_topic_shortcuts_are_ordinary_user_messages(self) -> None:
        session = create_new(self.paths, conversation_id="_theme_free_cli_test")
        submitted: list[str] = []

        class FakeAgent:
            def __init__(self) -> None:
                self.session = session
                self.cancel_event = SimpleNamespace(clear=lambda: None)
                self.executor = SimpleNamespace(registry=None)

            def run_turn(self, text: str) -> SimpleNamespace:
                submitted.append(text)
                return SimpleNamespace(finish_reason="stop", notices=[], assistant_text="")

        outputs: list[str] = []
        repl = ConversationRepl(
            paths=self.paths,
            session=session,
            agent=FakeAgent(),  # type: ignore[arg-type]
            input_fn=lambda _prompt: "",
            output_fn=outputs.append,
        )
        for text in ("主题 coding", "加主题 workflow", "换主题"):
            self.assertEqual(repl.handle_line(text), "continue")

        self.assertEqual(submitted, ["主题 coding", "加主题 workflow", "换主题"])
        self.assertEqual(session.meta.topics, [])
        self.assertFalse(any("主题未变更" in line or "已确认主题" in line for line in outputs))

    def test_session_banner_does_not_display_topic_metadata(self) -> None:
        session = create_new(self.paths, conversation_id="_theme_free_banner_test")
        session.set_topics(["coding"], phase="S4")
        outputs: list[str] = []
        repl = ConversationRepl(
            paths=self.paths,
            session=session,
            agent=SimpleNamespace(),  # type: ignore[arg-type]
            input_fn=lambda _prompt: "",
            output_fn=outputs.append,
        )

        repl._print_session_banner()

        self.assertEqual(len(outputs), 1)
        self.assertIn("mode:", outputs[0])
        self.assertNotIn("topics:", outputs[0])
        self.assertNotIn("coding", outputs[0])


if __name__ == "__main__":
    unittest.main()
