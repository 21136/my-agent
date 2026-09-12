import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from interface_lock import InterfaceLockGuard, read_lock
from terminal_shutdown import TerminalShutdownHooks, cleanup_terminal_session
from tests.isolation_helpers import make_temp_agent_paths


class TerminalShutdownTests(unittest.TestCase):
    def test_cleanup_releases_interface_lock(self) -> None:
        paths = make_temp_agent_paths(self)
        lock_guard = InterfaceLockGuard(paths, "terminal")
        lock_guard.acquire(takeover=False, interactive_takeover=False)
        repl = mock.Mock()
        repl._stop = False
        repl._ink_bridge = None

        cleanup_terminal_session(lock_guard, repl)

        self.assertIsNone(read_lock(paths))
        self.assertTrue(repl._stop)

    def test_shutdown_hooks_run_once(self) -> None:
        paths = make_temp_agent_paths(self)
        lock_guard = InterfaceLockGuard(paths, "terminal")
        lock_guard.acquire(takeover=False, interactive_takeover=False)
        hooks = TerminalShutdownHooks(lock_guard=lock_guard)
        hooks.install()

        hooks.run()
        hooks.run()

        self.assertIsNone(read_lock(paths))

    def test_clear_terminal_lock_file_without_killing_holder(self) -> None:
        from interface_lock import clear_terminal_lock_file, write_lock

        paths = make_temp_agent_paths(self)
        write_lock(paths, "terminal")
        holder = clear_terminal_lock_file(paths)
        self.assertIsNotNone(holder)
        self.assertEqual(holder.ui, "terminal")
        self.assertIsNone(read_lock(paths))


if __name__ == "__main__":
    unittest.main()
