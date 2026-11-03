from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_card_visibility import (CardInput, RequirementInput, ImportantResult, ViewAck,
                                  ClockEvidence, decide_visibility)

BASE = datetime(2026, 10, 30, 0, 0, tzinfo=timezone.utc)


def fixture(kind="project"):
    card = CardInput(session_id="session-1", card_id="card-1", kind=kind,
        requirements=(RequirementInput("req-a", "completed"),),
        important_results=(ImportantResult("req-a", "result-1"),))
    ack = ViewAck(session_id="session-1", card_id="card-1", requirement_id="req-a",
                  result_id="result-1", viewed_at=BASE, verified=True)
    return card, (ack,)


def decision(card, acks, now=BASE + timedelta(minutes=30), *, trusted=True, previous=None):
    return decide_visibility(card, acks, ClockEvidence(now=now, trusted=trusted, previous_now=previous))


@pytest.mark.parametrize("kind", ["project", "independent"])
def test_exact_thirty_minutes_after_verified_ack_permits_hiding(kind):
    card, acks = fixture(kind)
    assert not decision(card, acks, BASE + timedelta(minutes=30) - timedelta(microseconds=1)).may_hide
    assert decision(card, acks, BASE + timedelta(minutes=30)).may_hide


@pytest.mark.parametrize("status", ["pending", "queued", "in_progress", "awaiting_confirmation", "blocked", "unknown", "stopped", "failed", "", None])
def test_any_non_completed_requirement_keeps_card(status):
    card, acks = fixture()
    card = replace(card, requirements=card.requirements + (RequirementInput("req-b", status),))
    assert not decision(card, acks).may_hide


def test_all_results_and_all_requirements_require_individual_ack_and_time():
    card, acks = fixture()
    card = replace(card, requirements=card.requirements + (RequirementInput("req-b", "completed"),),
                   important_results=card.important_results + (ImportantResult("req-b", "result-2"), ImportantResult("req-a", "result-3")))
    ack2 = ViewAck("session-1", "card-1", "req-b", "result-2", BASE, True)
    ack3 = ViewAck("session-1", "card-1", "req-a", "result-3", BASE + timedelta(minutes=1), True)
    assert not decision(card, acks + (ack2,)).may_hide
    assert not decision(card, acks + (ack2, ack3)).may_hide
    assert decision(card, acks + (ack2, ack3), BASE + timedelta(minutes=31)).may_hide


def test_new_result_needs_new_ack_and_new_wait():
    card, acks = fixture()
    card = replace(card, important_results=(ImportantResult("req-a", "result-new"),))
    assert not decision(card, acks).may_hide
    newer = ViewAck("session-1", "card-1", "req-a", "result-new", BASE + timedelta(minutes=31), True)
    assert not decision(card, (newer,), BASE + timedelta(minutes=60)).may_hide
    assert decision(card, (newer,), BASE + timedelta(minutes=61)).may_hide


@pytest.mark.parametrize("field,value", [("session_id", "other"), ("card_id", "other"),
                                        ("requirement_id", "other"), ("result_id", "other"),
                                        ("verified", False), ("verified", 1), ("viewed_at", None),
                                        ("viewed_at", BASE.replace(tzinfo=None)),
                                        ("viewed_at", BASE + timedelta(hours=1))])
def test_invalid_ack_identity_or_timestamp_is_conservative(field, value):
    card, acks = fixture()
    assert not decision(card, (replace(acks[0], **{field: value}),)).may_hide


def test_clock_untrusted_by_default_and_rollback():
    card, acks = fixture()
    assert not decide_visibility(card, acks, ClockEvidence(now=BASE + timedelta(hours=1))).may_hide
    assert not decision(card, acks, trusted=False).may_hide
    assert not decision(card, acks, previous=BASE + timedelta(hours=1)).may_hide
    assert not decision(card, acks, now=None).may_hide
    assert not decision(card, acks, now=BASE.replace(tzinfo=None)).may_hide
    assert not decision(card, acks, previous=BASE.replace(tzinfo=None)).may_hide


@pytest.mark.parametrize("card_transform", [
    lambda c: replace(c, important_results=()),
    lambda c: replace(c, requirements=()),
    lambda c: replace(c, session_id=""),
    lambda c: replace(c, kind="other"),
    lambda c: replace(c, important_results=(ImportantResult("other", "result-1"),)),
    lambda c: replace(c, requirements=c.requirements + (RequirementInput("req-a", "completed"),)),
    lambda c: replace(c, important_results=c.important_results + (ImportantResult("req-a", "result-1"),)),
    lambda c: replace(c, requirements=c.requirements + (RequirementInput("req-b", "completed"),)),
])
def test_missing_duplicate_or_ambiguous_data_never_hides(card_transform):
    card, acks = fixture()
    assert not decision(card_transform(card), acks).may_hide


def test_duplicate_conflicting_or_extra_acks_are_rejected():
    card, acks = fixture()
    assert not decision(card, acks + acks).may_hide
    assert not decision(card, acks + (replace(acks[0], result_id="another"),)).may_hide


def test_restart_requires_new_caller_clock_attestation_not_datetime_now():
    card, acks = fixture()
    assert not decide_visibility(card, acks, ClockEvidence(now=BASE + timedelta(days=1))).may_hide
    assert decision(card, acks, now=BASE + timedelta(days=1), trusted=True).may_hide


def test_attested_now_valid_across_offsets_without_assuming_local_clock():
    card, acks = fixture()
    east = timezone(timedelta(hours=8))
    now = (BASE + timedelta(minutes=30)).astimezone(east)
    assert decision(card, acks, now=now).may_hide


def test_forward_wall_clock_jump_without_trust_is_not_elapsed_proof():
    card, acks = fixture()
    jumped_now = BASE + timedelta(days=365)
    assert not decision(card, acks, now=jumped_now, trusted=False, previous=BASE).may_hide


def test_invalid_time_zone_fails_closed():
    from datetime import tzinfo
    class Broken(tzinfo):
        def utcoffset(self, dt):
            raise RuntimeError("synthetic bad clock")
    card, acks = fixture()
    assert not decision(card, acks, now=BASE.replace(tzinfo=Broken())).may_hide
