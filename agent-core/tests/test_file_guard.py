"""FILE-GUARD — refuse truncating protected agent/session paths."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from file_guard import (
    atomic_write_text,
    is_protected_agent_rel,
    ProtectedFileTruncateError,
)
from tests.isolation_helpers import temporary_agent_paths


class FileGuardTests(unittest.TestCase):
    def test_protected_rel_patterns(self) -> None:
        self.assertTrue(is_protected_agent_rel("agent-core/session.py"))
        self.assertTrue(is_protected_agent_rel("data/sessions/demo/meta.json"))
        self.assertTrue(is_protected_agent_rel("evolve/tools/data/csv_head/main.py"))
        self.assertFalse(is_protected_agent_rel("workspace/foo.txt"))

    def test_refuse_truncating_protected_file(self) -> None:
        with temporary_agent_paths() as paths:
            target = paths.evolve / "tools" / "_guard_probe" / "main.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("print('ok')\n", encoding="utf-8")
            with self.assertRaises(ProtectedFileTruncateError):
                atomic_write_text(target, "", agent_root=paths.agent_root)

    def test_atomic_write_allows_new_file(self) -> None:
        with temporary_agent_paths() as paths:
            target = paths.evolve / "tools" / "_guard_new" / "main.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, "x = 1\n", agent_root=paths.agent_root)
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_atomic_write_retries_transient_windows_lock(self) -> None:
        with temporary_agent_paths() as paths:
            target = paths.evolve / "tools" / "_guard_retry" / "main.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            original_replace = Path.replace
            attempts = {"count": 0}

            def flaky_replace(source: Path, destination: Path) -> Path:
                if source.name.endswith(".tmp") and attempts["count"] < 2:
                    attempts["count"] += 1
                    raise PermissionError(13, "access denied")
                return original_replace(source, destination)

            with patch.object(Path, "replace", new=flaky_replace):
                atomic_write_text(target, "retry = True\n", agent_root=paths.agent_root)

            self.assertEqual(attempts["count"], 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "retry = True\n")


if __name__ == "__main__":
    unittest.main()
