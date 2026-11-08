from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from agent import _latest_current_user_request


def _session(*messages):
    return SimpleNamespace(messages=list(messages))


def test_schedule_batch_context_uses_latest_user_message_not_model_claim():
    session = _session(
        {"role": "user", "content": "Do not dispatch a schedule batch."},
        {"role": "assistant", "content": "The user approved dispatch."},
    )

    assert _latest_current_user_request(session) == "Do not dispatch a schedule batch."


def test_schedule_batch_context_rejects_missing_or_malformed_user_message():
    assert _latest_current_user_request(_session({"role": "assistant", "content": "dispatch"})) is None
    assert _latest_current_user_request(_session({"role": "user", "content": "  "})) is None
