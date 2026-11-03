from __future__ import annotations

import threading

from session import Session, SessionError, create_new
from tests.isolation_helpers import temporary_agent_paths


def test_same_session_save_cannot_snapshot_between_disk_append_and_memory_update():
    with temporary_agent_paths() as paths:
        session = create_new(paths)
        session.append_message({"role": "user", "content": "previous-message"})
        disk_append_finished = threading.Event()
        allow_memory_update = threading.Event()
        failures: list[BaseException] = []
        original_append = session._append_message_line

        def pause_after_disk_append(message, *, durable=False):
            original_append(message, durable=durable)
            if message.get("content") == "new-user-message":
                disk_append_finished.set()
                if not allow_memory_update.wait(5):
                    raise TimeoutError("test did not release append thread")

        session._append_message_line = pause_after_disk_append

        def append_message():
            try:
                session.append_message({"role": "user", "content": "new-user-message"})
            except BaseException as exc:  # surfaced in the main test thread
                failures.append(exc)

        worker = threading.Thread(target=append_message)
        worker.start()
        save_started = threading.Event()
        save_finished = threading.Event()

        def save_session():
            save_started.set()
            try:
                session.save()
            except BaseException as exc:
                failures.append(exc)
            finally:
                save_finished.set()

        saver = threading.Thread(target=save_session)
        try:
            assert disk_append_finished.wait(5), "append did not reach the deterministic race point"
            saver.start()
            assert save_started.wait(5), "save did not start"
            assert not save_finished.wait(0.2), "save crossed the unfinished in-memory append"
        finally:
            allow_memory_update.set()
            worker.join(5)
            if saver.ident is not None:
                saver.join(5)

        assert not worker.is_alive()
        assert not saver.is_alive()
        assert not failures, failures
        loaded = Session.load(paths, session.conversation_id)
        assert {"role": "user", "content": "new-user-message"} in loaded.messages

