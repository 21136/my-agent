from current_dialogue_project_card_chat import (
    candidate_defers_chat_surface,
    project_scoped_snapshot_deferred_from_chat,
    worker_batch_should_defer_project_cards_to_chat,
)


def test_worker_batch_failure_defers_chat_surface():
    rows = [
        {
            "project_id": "p1",
            "status": "blocked",
            "model_turn_status": "blocked",
            "record_lifecycle_status": "blocked_recorded",
        },
    ]
    assert worker_batch_should_defer_project_cards_to_chat(rows) is True
    assert candidate_defers_chat_surface({"defer_chat_surface": True}) is True


def test_project_scoped_blocked_snapshot_deferred_from_chat():
    snapshot = {
        "kind": "project_scoped",
        "cards": [{
            "requirements": [{
                "status": "blocked",
                "latest_result": {"status": "unknown", "summary": '{"host_status": "blocked"}'},
            }],
        }],
    }
    assert project_scoped_snapshot_deferred_from_chat(snapshot) is True


def test_run_line_candidate_without_defer_still_allows_chat_delivery_flag():
    assert candidate_defers_chat_surface({"project_ids": ("a",)}) is False
