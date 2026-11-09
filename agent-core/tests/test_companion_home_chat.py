from __future__ import annotations

import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from companion_home_chat import companion_home_defers_project_scoped_chat
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


def test_unbound_desktop_companion_defers_project_scoped_chat():
    with temporary_agent_paths() as paths:
        session = create_new(paths, conversation_id="companion-home-chat")
        session.meta.harness = "desktop"
        session.meta.project_id = ""
        session.save()
        assert companion_home_defers_project_scoped_chat(session) is True

        session.meta.project_id = "music"
        session.save()
        assert companion_home_defers_project_scoped_chat(session) is False
