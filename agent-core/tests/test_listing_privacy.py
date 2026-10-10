"""User-facing listings omit agent bookkeeping unless the path names it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from codebase_index import should_index_file
from project_mode import create_project, list_project_docs, project_dir
from tests.isolation_helpers import temporary_agent_paths
from tools.builtin import glob_file_search, grep, list_dir, read_file
from tools.builtin.glob_file_search import _is_ignored_path


class ListingPrivacyTests(unittest.TestCase):
    def _project(self, paths):
        create_project(paths, "private-notes", project_template="light")
        root = project_dir(paths, "private-notes")
        (root / "NOTES.md").write_text("hello notes\n", encoding="utf-8")
        (root / "RUNAWAY-PROGRESS.md").write_text("internal log\n", encoding="utf-8")
        book = root / ".agent"
        book.mkdir()
        (book / "cache.json").write_text("{}\n", encoding="utf-8")
        hidden_doc = root / ".plan-agent" / "secret.md"
        hidden_doc.write_text("# secret\n", encoding="utf-8")
        return root

    def test_list_dir_hides_bookkeeping_unless_named(self) -> None:
        with temporary_agent_paths() as paths:
            self._project(paths)
            listed = list_dir.run({"path": "workspace/private-notes", "recursive": True}, paths=paths)
            self.assertTrue(listed.ok, listed.error)
            names = {item["name"] for item in listed.data["entries"]}
            self.assertIn("NOTES.md", names)
            self.assertNotIn(".plan-agent", names)
            self.assertNotIn(".agent", names)
            self.assertNotIn("RUNAWAY-PROGRESS.md", names)
            self.assertFalse(any(".plan-agent" in name or ".agent" in name for name in names))

            opened = list_dir.run({"path": "workspace/private-notes/.plan-agent"}, paths=paths)
            self.assertTrue(opened.ok, opened.error)
            opened_names = {item["name"] for item in opened.data["entries"]}
            self.assertIn("manifest.json", opened_names)

            direct = read_file.run(
                {"path": "workspace/private-notes/.plan-agent/manifest.json"},
                paths=paths,
            )
            self.assertTrue(direct.ok, direct.error)
            self.assertIn("manifest", direct.data.get("content", ""))

    def test_glob_and_grep_hide_bookkeeping_unless_named(self) -> None:
        with temporary_agent_paths() as paths:
            root = self._project(paths)
            (root / "src").mkdir()
            (root / "src" / "app.md").write_text("app\n", encoding="utf-8")
            with mock.patch("tools.builtin.glob_file_search.shutil.which", return_value=None):
                found = glob_file_search.run(
                    {"pattern": "*", "path": "workspace/private-notes"},
                    paths=paths,
                )
                nested = glob_file_search.run(
                    {"pattern": "**/*.md", "path": "workspace/private-notes"},
                    paths=paths,
                )
            self.assertTrue(found.ok, found.error)
            blob = "\n".join(found.data["paths"])
            self.assertIn("NOTES.md", blob)
            self.assertNotIn(".plan-agent", blob)
            self.assertNotIn(".agent", blob)
            self.assertNotIn("RUNAWAY-PROGRESS.md", blob)
            nested_blob = "\n".join(nested.data["paths"])
            self.assertIn("src/app.md", nested_blob)
            self.assertNotIn(".plan-agent", nested_blob)

            with mock.patch("tools.builtin.glob_file_search.shutil.which", return_value=None):
                named = glob_file_search.run(
                    {"pattern": ".plan-agent/*", "path": "workspace/private-notes"},
                    paths=paths,
                )
            self.assertTrue(named.ok, named.error)
            self.assertTrue(any(path.endswith("manifest.json") for path in named.data["paths"]))

            with mock.patch("tools.builtin.grep.shutil.which", return_value=None):
                scanned = grep.run(
                    {"pattern": "secret", "path": "workspace/private-notes"},
                    paths=paths,
                )
            self.assertTrue(scanned.ok, scanned.error)
            self.assertFalse(any(".plan-agent" in item["path"] for item in scanned.data["matches"]))

            with mock.patch("tools.builtin.grep.shutil.which", return_value=None):
                inside = grep.run(
                    {"pattern": "secret", "path": "workspace/private-notes/.plan-agent"},
                    paths=paths,
                )
            self.assertTrue(inside.ok, inside.error)
            self.assertTrue(any("secret.md" in item["path"] for item in inside.data["matches"]))

            self.assertTrue(_is_ignored_path(".plan-agent/manifest.json", search_root=root))
            self.assertFalse(
                _is_ignored_path(".plan-agent/manifest.json", search_root=root, explicit=".plan-agent/**")
            )
            self.assertFalse(
                should_index_file(
                    root / ".plan-agent" / "secret.md",
                    rel_posix=".plan-agent/secret.md",
                    search_root=root,
                )
            )

    def test_installed_search_tools_also_hide_bookkeeping(self) -> None:
        with temporary_agent_paths() as paths:
            self._project(paths)
            found = glob_file_search.run(
                {"pattern": "*", "path": "workspace/private-notes"},
                paths=paths,
            )
            self.assertTrue(found.ok, found.error)
            blob = "\n".join(found.data["paths"])
            self.assertIn("NOTES.md", blob)
            self.assertNotIn(".plan-agent", blob)
            self.assertNotIn("RUNAWAY-PROGRESS.md", blob)

            scanned = grep.run({"pattern": "secret|internal log|hello", "path": "workspace/private-notes"}, paths=paths)
            self.assertTrue(scanned.ok, scanned.error)
            paths_hit = "\n".join(item["path"] for item in scanned.data["matches"])
            self.assertIn("NOTES.md", paths_hit)
            self.assertNotIn(".plan-agent", paths_hit)
            self.assertNotIn("RUNAWAY-PROGRESS.md", paths_hit)

    def test_doc_catalog_skips_bookkeeping(self) -> None:
        with temporary_agent_paths() as paths:
            self._project(paths)
            listed = {item["path"] for item in list_project_docs(paths, "private-notes")}
            self.assertIn("NOTES.md", listed)
            self.assertNotIn("RUNAWAY-PROGRESS.md", listed)
            self.assertNotIn(".plan-agent/secret.md", listed)
            self.assertFalse(any(path.startswith(".plan-agent/") or path.startswith(".agent/") for path in listed))
