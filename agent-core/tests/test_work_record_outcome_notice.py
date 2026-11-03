from pathlib import Path
import sys
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from server import emit_work_record_outcome_notice


def test_capture_outcome_uses_existing_notice_event_and_only_public_message():
    events = []
    outcome = SimpleNamespace(
        status="recorded",
        message="已按本次授权写入当前会话工作记录。",
        item_id="synthetic-item-id",
        private_source="SYNTHETIC_RAW_TEXT",
    )

    emit_work_record_outcome_notice(events.append, outcome)

    assert events == [{
        "type": "turn.notice",
        "level": "info",
        "text": outcome.message,
    }]


def test_non_success_outcome_is_warn_and_still_uses_actual_message():
    events = []
    outcome = SimpleNamespace(status="not_authorized", message="未获本次外发授权；未外发、未写入。")

    emit_work_record_outcome_notice(events.append, outcome)

    assert events == [{"type": "turn.notice", "level": "warn", "text": outcome.message}]
