from __future__ import annotations

import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from server import _external_user_message_receipt_request_id


def test_record_bound_run_still_qualifies_for_user_message_persistence_receipt():
    request_id = "d1f67fa0-4db4-40ef-9727-0123456789ab"
    # Work-record lifecycle binding is intentionally absent from this decision:
    # it governs result capture, not whether the user row was persisted.
    assert _external_user_message_receipt_request_id(
        request_id,
        prestarted_run_id=None,
        attachments=(),
        attachment_ids=(),
        current_session=object(),
        attachment_session=None,
    ) == request_id


def test_receipt_eligibility_rejects_prestarted_or_unbound_attachment_turns():
    request_id = "d1f67fa0-4db4-40ef-9727-0123456789ab"
    session = object()
    assert _external_user_message_receipt_request_id(
        request_id, prestarted_run_id="run-1", attachments=(), attachment_ids=(),
        current_session=session, attachment_session=None,
    ) is None
    assert _external_user_message_receipt_request_id(
        request_id, prestarted_run_id=None, attachments=(object(),), attachment_ids=("file-1",),
        current_session=session, attachment_session=object(),
    ) is None
