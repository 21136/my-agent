from __future__ import annotations

import pytest

from file_stage import FileStageStore, StagedAttachment


def _attachment(attachment_id: str, name: str) -> StagedAttachment:
    return StagedAttachment(
        id=attachment_id,
        name=name,
        ref=f"workspace/_drops/{name}",
        size=1,
        mime="text/plain",
        readable_text=True,
        copied=True,
    )


def test_take_consumes_all_valid_ids_in_requested_order():
    store = FileStageStore()
    first = _attachment("first", "first.txt")
    second = _attachment("second", "second.txt")
    store.register("session-a", first)
    store.register("session-a", second)

    assert store.take("session-a", ["second", "first"]) == [second, first]
    assert store.take("session-a", ["first", "second"]) == []


@pytest.mark.parametrize("ids", [
    ["missing", "first", "second"],
    ["first", "missing", "second"],
    ["first", "second", "missing"],
])
def test_invalid_id_at_any_batch_position_consumes_nothing(ids):
    store = FileStageStore()
    first = _attachment("first", "first.txt")
    second = _attachment("second", "second.txt")
    store.register("session-a", first)
    store.register("session-a", second)

    assert store.take("session-a", ids) == []
    assert store.take("session-a", ["first", "second"]) == [first, second]


def test_duplicate_id_is_rejected_without_consuming_any_item():
    store = FileStageStore()
    first = _attachment("first", "first.txt")
    second = _attachment("second", "second.txt")
    store.register("session-a", first)
    store.register("session-a", second)

    assert store.take("session-a", ["first", "first", "second"]) == []
    assert store.take("session-a", ["first", "second"]) == [first, second]


def test_cross_session_id_is_rejected_without_consuming_source_item():
    store = FileStageStore()
    item = _attachment("shared-id", "same-session-only.txt")
    store.register("session-a", item)

    assert store.take("session-b", ["shared-id"]) == []
    assert store.take("session-a", ["shared-id"]) == [item]


def test_empty_take_is_a_noop_and_keeps_staged_items_available():
    store = FileStageStore()
    item = _attachment("first", "first.txt")
    store.register("session-a", item)

    assert store.take("session-a", []) == []
    assert store.take("session-a", ["first"]) == [item]
    assert store.take("session-without-bucket", []) == []
