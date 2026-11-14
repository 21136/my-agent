from __future__ import annotations

import concurrent.futures
import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_doc_view import ProjectDocumentViewStore, ProjectDocViewError
from project_mode import project_dir
from session import create_new
from tests.isolation_helpers import temporary_agent_paths


class IndependentProjectDocViewTests(unittest.TestCase):
    def setUp(self):
        self._paths_ctx = temporary_agent_paths(prefix="doc-view-")
        self.paths = self._paths_ctx.__enter__()
        self.pid = "view-demo"
        self.root = project_dir(self.paths, self.pid)
        self.root.mkdir(parents=True)
        (self.root / "PROJECT.md").write_text("# Initial\n", encoding="utf-8")
        self.session = create_new(self.paths)
        self.other_session = create_new(self.paths)
        self.store = ProjectDocumentViewStore(self.paths)
        self.view_id = self.store.authorize(self.session, self.pid, approved=True)

    def tearDown(self):
        self._paths_ctx.__exit__(None, None, None)

    def test_project_is_listed_and_read_write_are_session_unbound_and_versioned(self):
        listed = self.store.list(self.view_id, self.session, self.pid, request_id="l1")
        self.assertEqual(listed["project_id"], self.pid)
        self.assertEqual(listed["docs"][0]["path"], "PROJECT.md")
        read = self.store.read(self.view_id, self.session, self.pid, "PROJECT.md", request_id="r1")
        self.assertEqual(read["type"], "project.doc.view.read.done")
        self.assertEqual(read["request_id"], "r1")
        self.assertEqual(read["content"], "# Initial\n")
        saved = self.store.write(self.view_id, self.session, self.pid, "PROJECT.md", "# Saved\n",
                                 base_mtime=read["mtime"], base_hash=read["content_hash"], request_id="w1")
        self.assertEqual(saved["type"], "project.doc.view.write.done")
        self.assertEqual(saved["request_id"], "w1")
        self.assertEqual(saved["content_hash"], self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")["content_hash"])
        self.assertEqual((self.root / "PROJECT.md").read_text(encoding="utf-8"), "# Saved\n")

    def test_missing_version_force_and_external_conflict_never_overwrite(self):
        with self.assertRaises(ProjectDocViewError) as missing:
            self.store.write(self.view_id, self.session, self.pid, "PROJECT.md", "# Bad", request_id="w2")
        self.assertEqual(missing.exception.code, "invalid_version")
        read = self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")
        (self.root / "PROJECT.md").write_text("# External\n", encoding="utf-8")
        with self.assertRaises(ProjectDocViewError) as conflict:
            self.store.write(self.view_id, self.session, self.pid, "PROJECT.md", "# Overwrite",
                             base_mtime=read["mtime"], base_hash=read["content_hash"], request_id="w3")
        self.assertEqual(conflict.exception.code, "doc_conflict")
        self.assertEqual((self.root / "PROJECT.md").read_text(encoding="utf-8"), "# External\n")

    def test_write_conflicts_when_mtime_changes_even_if_content_hash_does_not(self):
        import os
        read = self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")
        path = self.root / "PROJECT.md"
        os.utime(path, (read["mtime"] + 5, read["mtime"] + 5))
        with self.assertRaises(ProjectDocViewError) as conflict:
            self.store.write(self.view_id, self.session, self.pid, "PROJECT.md", "# changed\n",
                             base_mtime=read["mtime"], base_hash=read["content_hash"])
        self.assertEqual(conflict.exception.code, "doc_conflict")
        self.assertEqual(conflict.exception.current["content_hash"], read["content_hash"])
        self.assertEqual(path.read_text(encoding="utf-8"), "# Initial\n")

    def test_rejects_project_mismatch_path_escape_and_symlink(self):
        with self.assertRaises(ProjectDocViewError):
            self.store.read(self.view_id, self.session, "another-project", "PROJECT.md")
        for unsafe in ("../outside.md", str(self.root / "PROJECT.md"), "linked.md"):
            if unsafe == "linked.md":
                target = self.root.parent / "outside.md"
                target.write_text("# secret", encoding="utf-8")
                try:
                    (self.root / unsafe).symlink_to(target)
                except (OSError, NotImplementedError):
                    continue
            with self.assertRaises(ProjectDocViewError):
                self.store.read(self.view_id, self.session, self.pid, unsafe)

    def test_session_change_revokes_view_and_rejection_never_grants(self):
        with self.assertRaises(ProjectDocViewError):
            self.store.read(self.view_id, self.other_session, self.pid, "PROJECT.md")
        self.assertFalse(self.store.authorize(self.session, self.pid, approved=False))

    def test_project_root_replacement_revokes_view(self):
        moved = self.root.with_name(self.root.name + "-old")
        self.root.rename(moved)
        self.root.mkdir()
        (self.root / "PROJECT.md").write_text("# Replacement\n", encoding="utf-8")
        with self.assertRaises(ProjectDocViewError):
            self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")

    def test_ws_bridge_session_transition_and_disconnect_revoke_views(self):
        from server import WsBridge
        bridge = WsBridge(emit=lambda _event: None, paths=self.paths)
        bridge._project_doc_view_store.authorize(self.session, self.pid, approved=True)
        bridge.begin_session_binding_change()
        self.assertFalse(bridge._project_doc_view_store._views)
        bridge.end_session_binding_change()

        second = WsBridge(emit=lambda _event: None, paths=self.paths)
        second._project_doc_view_store.authorize(self.session, self.pid, approved=True)
        second.mark_connection_closed()
        self.assertFalse(second._project_doc_view_store._views)

    def test_view_id_is_not_portable_between_connections(self):
        other_store = ProjectDocumentViewStore(self.paths)
        with self.assertRaises(ProjectDocViewError):
            other_store.read(self.view_id, self.session, self.pid, "PROJECT.md")

    def test_session_transition_revokes_prior_view(self):
        self.store.revoke_all()
        with self.assertRaises(ProjectDocViewError):
            self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")

    def test_ws_authorization_rejects_denial_and_session_change_during_confirmation(self):
        import asyncio
        from threading import Event
        from types import SimpleNamespace
        from server import WsSessionHandler

        for change_session, choice in ((False, "n"), (True, "y")):
            events = []
            current_session = [self.session]
            class Bridge:
                _connection_closed = Event()
                _project_doc_view_store = ProjectDocumentViewStore(self.paths)
                _session_provider = staticmethod(lambda: current_session[0])
                def emit(self, event): events.append(event)
                def confirm_fn(self, _preview, _allow_all):
                    if change_session:
                        current_session[0] = self.outer.other_session
                    return choice
            bridge = Bridge()
            bridge.outer = self
            handler = object.__new__(WsSessionHandler)
            asyncio.run(handler._dispatch_project_doc_view(
                {"type": "project.doc.view.authorize", "request_id": "auth-denied", "project_id": self.pid},
                SimpleNamespace(session=self.session), bridge))
            self.assertEqual(events[-1]["type"], "project.doc.view.error")
            self.assertNotIn("view_id", events[-1])

    def test_ws_authorization_rejects_completed_session_transition(self):
        import asyncio
        from threading import Event
        from types import SimpleNamespace
        from server import WsSessionHandler

        events = []
        class Bridge:
            _connection_closed = Event()
            _project_doc_view_store = ProjectDocumentViewStore(self.paths)
            _session_provider = staticmethod(lambda: self.session)
            def emit(self, event): events.append(event)
            def confirm_fn(self, _preview, _allow_all):
                # Simulate switch away and back while the user confirmation is pending.
                self._project_doc_view_store.revoke_all()
                return "y"
        bridge = Bridge()
        handler = object.__new__(WsSessionHandler)
        asyncio.run(handler._dispatch_project_doc_view(
            {"type": "project.doc.view.authorize", "request_id": "auth-race", "project_id": self.pid},
            SimpleNamespace(session=self.session), bridge))
        self.assertEqual(events[-1]["type"], "project.doc.view.error")
        self.assertEqual(events[-1]["code"], "permission_denied")
        self.assertNotIn("view_id", events[-1])

    def test_ws_conflict_response_echoes_identity_and_never_returns_document_body(self):
        import asyncio
        from threading import Event
        from types import SimpleNamespace
        from server import WsSessionHandler

        events = []
        class Bridge:
            _connection_closed = Event()
            _session_provider = staticmethod(lambda: self.session)
            _project_doc_view_store = self.store
            def emit(self, event): events.append(event)
        bridge = Bridge()
        read = self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")
        (self.root / "PROJECT.md").write_text("# External body\n", encoding="utf-8")
        handler = object.__new__(WsSessionHandler)
        asyncio.run(handler._dispatch_project_doc_view({
            "type": "project.doc.view.write", "request_id": "save-conflict", "project_id": self.pid,
            "view_id": self.view_id, "path": "PROJECT.md", "content": "# overwrite",
            "base_mtime": read["mtime"], "base_hash": read["content_hash"],
        }, SimpleNamespace(session=self.session), bridge))
        event = events[-1]
        self.assertEqual((event["type"], event["request_id"], event["project_id"], event["path"]),
                         ("project.doc.view.error", "save-conflict", self.pid, "PROJECT.md"))
        self.assertEqual(event["code"], "doc_conflict")
        self.assertEqual(set(event["current"]), {"mtime", "content_hash", "size"})
        self.assertNotIn("content", event)
        self.assertEqual((self.root / "PROJECT.md").read_text(encoding="utf-8"), "# External body\n")

    def test_force_field_is_rejected_even_when_false(self):
        read = self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")
        with self.assertRaises(ProjectDocViewError) as rejected:
            self.store.write(self.view_id, self.session, self.pid, "PROJECT.md", "# no",
                             base_mtime=read["mtime"], base_hash=read["content_hash"], force=False)
        self.assertEqual(rejected.exception.code, "force_not_supported")
        with self.assertRaises(ProjectDocViewError) as null_force:
            self.store.write(self.view_id, self.session, self.pid, "PROJECT.md", "# no",
                             base_mtime=read["mtime"], base_hash=read["content_hash"], force=None)
        self.assertEqual(null_force.exception.code, "force_not_supported")

    def test_ws_bridge_confirmation_receipt_is_run_scoped_and_view_is_revoked(self):
        import asyncio
        import time
        from threading import Thread
        from types import SimpleNamespace
        from server import WsBridge, WsSessionHandler

        events = []
        bridge = WsBridge(emit=events.append, paths=self.paths, confirm_timeout=3.0)
        bridge._session_provider = lambda: self.session
        bridge.lifecycle_conversation_id = self.session.conversation_id
        self.assertTrue(bridge.begin_turn(mode="ordinary"))
        run_id = bridge.lifecycle.snapshot().run_id
        handler = object.__new__(WsSessionHandler)
        repl = SimpleNamespace(session=self.session)
        outcome = []
        worker = Thread(target=lambda: outcome.append(asyncio.run(handler._dispatch_project_doc_view(
            {"type": "project.doc.view.authorize", "request_id": "real-auth", "project_id": self.pid},
            repl, bridge))))
        worker.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(e.get("type") == "confirm.request" for e in events):
            time.sleep(0.01)
        confirmation = next((event for event in events if event.get("type") == "confirm.request"), None)
        self.assertIsNotNone(confirmation)
        self.assertEqual(confirmation.get("session_id"), self.session.conversation_id)
        self.assertEqual(confirmation.get("run_id"), run_id)
        self.assertTrue(bridge.deliver_confirm(confirmation["request_id"], "y"))
        worker.join(timeout=3.0)
        self.assertFalse(worker.is_alive())
        authorized = next(e for e in events if e.get("type") == "project.doc.view.authorized")
        self.assertEqual(authorized["request_id"], "real-auth")
        self.assertEqual(bridge.lifecycle.snapshot().state, "running")

        # Exercise the actual handler protocol with the connection-owned WsBridge,
        # not only the store API: authorized -> read -> versioned write.
        asyncio.run(handler._dispatch_project_doc_view({
            "type": "project.doc.view.read", "request_id": "real-read",
            "project_id": self.pid, "view_id": authorized["view_id"], "path": "PROJECT.md",
        }, repl, bridge))
        read_event = events[-1]
        self.assertEqual((read_event["type"], read_event["request_id"]),
                         ("project.doc.view.read.done", "real-read"))
        asyncio.run(handler._dispatch_project_doc_view({
            "type": "project.doc.view.write", "request_id": "real-write",
            "project_id": self.pid, "view_id": authorized["view_id"], "path": "PROJECT.md",
            "content": "# Via handler\n", "base_mtime": read_event["mtime"],
            "base_hash": read_event["content_hash"],
        }, repl, bridge))
        self.assertEqual((events[-1]["type"], events[-1]["request_id"]),
                         ("project.doc.view.write.done", "real-write"))
        self.assertEqual((self.root / "PROJECT.md").read_text(encoding="utf-8"), "# Via handler\n")

        bridge.begin_session_binding_change()
        bridge.end_session_binding_change()
        events.clear()
        asyncio.run(handler._dispatch_project_doc_view({
            "type": "project.doc.view.list", "request_id": "after-switch", "project_id": self.pid,
            "view_id": authorized["view_id"],
        }, repl, bridge))
        self.assertEqual(events[-1]["type"], "project.doc.view.error")
        self.assertEqual(events[-1]["code"], "permission_denied")
        self.assertEqual(bridge.lifecycle.snapshot().state, "running")
        bridge.finish_execution("test_complete", ok=True, run_id=run_id)

    def test_ws_authorization_uses_confirmation_and_never_trusts_renderer_claim(self):
        import asyncio
        from threading import Event
        from types import SimpleNamespace
        from server import WsSessionHandler

        events = []
        confirmations = []
        current_session = [self.session]
        class Bridge:
            _connection_closed = Event()
            _project_doc_view_store = ProjectDocumentViewStore(self.paths)
            _session_provider = staticmethod(lambda: current_session[0])
            def emit(self, event): events.append(event)
            def confirm_fn(self, preview, allow_all):
                confirmations.append((preview, allow_all))
                return "y"
        bridge = Bridge()
        repl = SimpleNamespace(session=self.session)
        handler = object.__new__(WsSessionHandler)
        asyncio.run(handler._dispatch_project_doc_view(
            {"type": "project.doc.view.authorize", "request_id": "auth-1", "project_id": self.pid, "approved": True}, repl, bridge))
        self.assertEqual(events[-1]["type"], "project.doc.view.authorized")
        self.assertEqual(len(confirmations), 1)
        self.assertIn("不会切换或绑定当前会话", confirmations[0][0])
        self.assertFalse(confirmations[0][1])
        # The client-supplied approved flag did not bypass the real confirm callback.
        self.assertTrue(events[-1].get("view_id"))

    def test_cross_connection_concurrent_saves_with_same_version_allow_one(self):
        other_store = ProjectDocumentViewStore(self.paths)
        other_view = other_store.authorize(self.session, self.pid, approved=True)
        read = self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")
        barrier = __import__("threading").Barrier(2)
        results = []
        def save(store, view, content):
            barrier.wait()
            try:
                return store.write(view, self.session, self.pid, "PROJECT.md", content,
                                   base_mtime=read["mtime"], base_hash=read["content_hash"])
            except ProjectDocViewError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda args: save(*args), [
                (self.store, self.view_id, "# from one\n"),
                (other_store, other_view, "# from two\n"),
            ]))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(results.count("doc_conflict"), 1)

    def test_windows_unsafe_component_names_are_rejected(self):
        for path in ("folder:stream.md", "CON.md", "nul.txt.md", "NUL .md", "bad. /file.md", "folder./file.md", "COM¹.md"):
            with self.subTest(path=path), self.assertRaises(ProjectDocViewError):
                self.store._validate_path(self.root, path)

    def test_secure_document_handle_is_pinned_against_path_replacement(self):
        import os
        from project_doc_view import _open_secure_document, _read_open_document

        nested = self.root / "nested"
        nested.mkdir()
        target = nested / "NOTE.md"
        target.write_text("# pinned\n", encoding="utf-8")
        _pid, lease, root = self.store._authorized_root(self.view_id, self.session, self.pid)
        with _open_secure_document(root, "nested/NOTE.md", lease.root_identity) as (fd, _st):
            moved = nested.with_name("nested-moved")
            if os.name == "nt":
                # Windows handle sharing deliberately denies rename/delete while
                # parent and leaf handles are active.
                with self.assertRaises(OSError):
                    nested.rename(moved)
                with self.assertRaises(OSError):
                    target.rename(target.with_name("NOTE-held.md"))
                self.assertEqual(_read_open_document(fd), "# pinned\n")
            else:
                nested.rename(moved)
                self.assertEqual(_read_open_document(fd), "# pinned\n")
                moved.rename(nested)
        self.assertEqual(target.read_text(encoding="utf-8"), "# pinned\n")

    def test_document_list_rejects_symlink_subtree_without_descending(self):
        external = self.root.parent / "external-tree"
        external.mkdir()
        (external / "secret.md").write_text("# secret", encoding="utf-8")
        link = self.root / "linked-tree"
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink unavailable")
        listed = self.store.list(self.view_id, self.session, self.pid)
        self.assertNotIn("linked-tree/secret.md", [item["path"] for item in listed["docs"]])

    def test_revocation_waits_for_inflight_write_commit_boundary(self):
        import threading
        read = self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")
        entered = threading.Event()
        resume = threading.Event()
        original = self.store._authorized_root
        def gated(*args):
            entered.set()
            resume.wait(2)
            return original(*args)
        self.store._authorized_root = gated
        outcome = []
        def attempt_write():
            try:
                outcome.append(self.store.write(
                    self.view_id, self.session, self.pid, "PROJECT.md", "# after revoke\n",
                    base_mtime=read["mtime"], base_hash=read["content_hash"]))
            except ProjectDocViewError as exc:
                outcome.append(exc.code)
        writer = threading.Thread(target=attempt_write)
        writer.start()
        self.assertTrue(entered.wait(1))
        revoker = threading.Thread(target=self.store.revoke_all)
        revoker.start()
        resume.set()
        writer.join(2)
        revoker.join(2)
        self.assertFalse(writer.is_alive())
        self.assertFalse(revoker.is_alive())
        self.assertEqual(outcome, ["permission_denied"], "revocation must prevent a later write commit")

    def test_two_concurrent_saves_with_same_version_allow_only_one(self):
        read = self.store.read(self.view_id, self.session, self.pid, "PROJECT.md")
        def save(text):
            try:
                return self.store.write(self.view_id, self.session, self.pid, "PROJECT.md", text,
                                        base_mtime=read["mtime"], base_hash=read["content_hash"])
            except ProjectDocViewError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ("# one\n", "# two\n")))
        self.assertEqual(sum(isinstance(x, dict) for x in results), 1)
        self.assertEqual(results.count("doc_conflict"), 1)


if __name__ == "__main__":
    unittest.main()
