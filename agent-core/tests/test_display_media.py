"""Local display path policy: user files allowed, secrets denied."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from display_media import (
    LOCAL_DISPLAY_PREFIX,
    is_display_denied,
    resolve_display_path,
    search_user_media,
    to_display_ref,
)
from host_scope import HostPathDeniedError
from tests.isolation_helpers import make_temp_agent_paths
from tools.builtin.show_images import run

PNG_BYTES = b"\x89PNG\r\n\x1a\n"


class DisplayMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)

    def test_outside_agent_root_uses_local_ref(self) -> None:
        outside = Path(self.paths.agent_root).resolve().parent / "outside-preview.png"
        outside.write_bytes(PNG_BYTES)
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        resolved = resolve_display_path(self.paths, str(outside))
        self.assertEqual(resolved, outside.resolve())
        ref = to_display_ref(self.paths, resolved)
        self.assertTrue(ref.startswith(LOCAL_DISPLAY_PREFIX))
        result = run({"paths": [str(outside)]}, paths=self.paths)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["display_paths"], [ref])

    def test_denies_ssh_private_key(self) -> None:
        ssh = Path(self.paths.agent_root).resolve().parent / ".ssh" / "id_rsa"
        ssh.parent.mkdir(parents=True, exist_ok=True)
        ssh.write_text("secret", encoding="utf-8")
        self.addCleanup(lambda: ssh.unlink(missing_ok=True))
        self.assertTrue(is_display_denied(ssh))
        with self.assertRaises(HostPathDeniedError):
            resolve_display_path(self.paths, str(ssh))

    def test_agent_relative_image_still_works(self) -> None:
        rel = "workspace/demo/x.png"
        target = self.paths.resolve_under_agent(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PNG_BYTES)
        result = run({"paths": [rel]}, paths=self.paths)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["display_paths"], [rel])

    def _personal_home(self) -> Path:
        home = Path(self.paths.data) / "fake-home"
        desktop = home / "Desktop"
        downloads = home / "Downloads"
        desktop.mkdir(parents=True)
        downloads.mkdir(parents=True)
        return home

    def test_search_unique_desktop_filename(self) -> None:
        home = self._personal_home()
        target = home / "Desktop" / "列车快门.png"
        target.write_bytes(PNG_BYTES)
        (home / "Downloads" / "other.png").write_bytes(PNG_BYTES)
        found = search_user_media("桌面上的列车快门", home=home)
        self.assertEqual(found.status, "selected")
        assert found.selected is not None
        self.assertEqual(found.selected.path.resolve(), target.resolve())

    def test_search_ambiguous_asks(self) -> None:
        home = self._personal_home()
        (home / "Desktop" / "列车-a.png").write_bytes(PNG_BYTES)
        (home / "Desktop" / "列车-b.png").write_bytes(PNG_BYTES)
        found = search_user_media("列车", home=home)
        self.assertEqual(found.status, "ambiguous")
        self.assertGreaterEqual(len(found.candidates), 2)

    def test_show_images_query_displays_unique_hit(self) -> None:
        home = self._personal_home()
        target = home / "Desktop" / "列车快门.png"
        target.write_bytes(PNG_BYTES)
        with patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            result = run({"query": "列车快门"}, paths=self.paths)
        self.assertTrue(result.ok)
        self.assertTrue(result.data["display_paths"][0].endswith("列车快门.png"))

    def test_show_images_query_too_broad(self) -> None:
        home = self._personal_home()
        (home / "Desktop" / "a.png").write_bytes(PNG_BYTES)
        with patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            result = run({"query": "桌面上的图片"}, paths=self.paths)
        self.assertTrue(result.ok)
        self.assertEqual(result.data.get("status"), "query_too_broad")
        self.assertEqual(result.data.get("display_paths"), [])

    def test_shows_typescript_file(self) -> None:
        rel = "workspace/demo/app.ts"
        target = self.paths.resolve_under_agent(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const n = 1;\n", encoding="utf-8")
        result = run({"paths": [rel]}, paths=self.paths)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["display_paths"], [rel])

    def test_search_finds_markdown_on_desktop(self) -> None:
        home = self._personal_home()
        target = home / "Desktop" / "notes.md"
        target.write_text("# hi\n", encoding="utf-8")
        found = search_user_media("notes.md", home=home)
        self.assertEqual(found.status, "selected")
        assert found.selected is not None
        self.assertEqual(found.selected.path.resolve(), target.resolve())


if __name__ == "__main__":
    unittest.main()
