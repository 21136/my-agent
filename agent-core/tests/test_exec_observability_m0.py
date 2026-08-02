"""Phase 27 M0 — services WS API + chat-state contract (IT-90 / IT-92)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
_ROOT = _AGENT_CORE.parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from services_api import DEFAULT_MAX_CHARS, DEFAULT_TAIL_LINES, dispatch_services_message
from tests.isolation_helpers import temporary_agent_paths


class TestExecObservabilityM0(unittest.TestCase):
    def test_it90_confirm_labels_not_done_yet(self) -> None:
        """IT-90: confirm must not use bare 「已执行」 as success."""
        chat_state = _ROOT / "desktop" / "src" / "shells" / "chat-state.ts"
        text = chat_state.read_text(encoding="utf-8")
        self.assertIn('y: "已同意，执行中…"', text)
        self.assertIn('a: "本会话均允许 · 执行中…"', text)
        self.assertIn("formatToolElapsed", text)
        self.assertNotRegex(text, r'y:\s*"已执行"')

    def test_it92_services_list_and_logs(self) -> None:
        """IT-92: services.list / services.logs via services_api."""
        with temporary_agent_paths(copy_tool_dirs=("common/run_service",)) as paths:
            services_dir = paths.data / "services"
            services_dir.mkdir(parents=True, exist_ok=True)
            (services_dir / "demo.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "status": "stopped",
                        "alive": False,
                        "pid": None,
                        "ready_port": 8080,
                        "command": "echo hi",
                    }
                ),
                encoding="utf-8",
            )
            (services_dir / "demo.log").write_text(
                "\n".join(f"line-{i}" for i in range(80)),
                encoding="utf-8",
            )

            listed = dispatch_services_message(paths, {"type": "services.list"})
            self.assertEqual(listed["type"], "services.list.done")
            self.assertTrue(listed["ok"])
            names = [s["name"] for s in listed["services"]]
            self.assertIn("demo", names)
            demo = next(s for s in listed["services"] if s["name"] == "demo")
            self.assertEqual(demo.get("ready_port"), 8080)

            logs = dispatch_services_message(
                paths,
                {"type": "services.logs", "name": "demo", "tail_lines": DEFAULT_TAIL_LINES},
            )
            self.assertEqual(logs["type"], "services.logs.done")
            self.assertEqual(logs["name"], "demo")
            self.assertLessEqual(len(logs["text"]), DEFAULT_MAX_CHARS)
            self.assertIn("line-79", logs["text"])
            self.assertNotIn("line-0", logs["text"])


if __name__ == "__main__":
    unittest.main()
