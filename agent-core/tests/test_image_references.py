"""On-demand image-reference lookup across local chat history."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from host_scope import HostRoot, HostScopeConfig, save_host_scope, utc_now_iso
from image_references import find_image_references
from tests.isolation_helpers import make_temp_agent_paths


class ImageReferenceSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = make_temp_agent_paths(self)
        self.host_root = Path(tempfile.mkdtemp(prefix="image-ref-host-"))
        self.addCleanup(shutil.rmtree, self.host_root, ignore_errors=True)

    def _write_session(
        self,
        session_id: str,
        title: str,
        messages: list[dict[str, str]],
        *,
        harness: str = "desktop",
    ) -> Path:
        session_dir = self.paths.data / "sessions" / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "goal.md").write_text(title, encoding="utf-8")
        (session_dir / "meta.json").write_text(
            json.dumps({"harness": harness, "updated_at": "2026-10-20T12:00:00Z"}),
            encoding="utf-8",
        )
        with (session_dir / "messages.jsonl").open("w", encoding="utf-8") as handle:
            for message in messages:
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")
        return session_dir

    def _write_png(self, ref: str) -> None:
        path = self.paths.resolve_under_agent(ref, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\nimage-fixture")

    @staticmethod
    def _attachment(name: str, ref: str, body: str) -> dict[str, str]:
        return {
            "role": "user",
            "content": (
                "[附件]\n"
                f"- {name} → {ref} (1.0 KB, image/png；不可直接 read_file；图片附件)\n\n"
                f"{body}"
            ),
        }

    def test_context_search_finds_a_reference_in_another_local_session(self) -> None:
        ref = "workspace/_drops/old-chat/lake.png"
        self._write_png(ref)
        self._write_session(
            "old-chat",
            "日月潭周末旅行",
            [
                {"role": "user", "content": "周末准备去日月潭旅行。"},
                {"role": "assistant", "content": "我可以帮你整理路线。"},
                self._attachment("lake.png", ref, "这是日月潭湖边的照片。"),
            ],
        )

        result = find_image_references(
            "日月潭湖边照片",
            paths=self.paths,
            current_session_dir=None,
        )

        self.assertEqual(result.status, "selected")
        self.assertIsNotNone(result.selected)
        self.assertEqual(result.selected.filename, "lake.png")
        self.assertEqual(result.selected.session_title, "日月潭周末旅行")
        self.assertIn("日月潭", result.selected.context)
        self.assertTrue(result.selected.path.is_file())
        self.assertNotIn(str(result.selected.path), json.dumps(result.to_public_dict(), ensure_ascii=False))

    def test_equal_context_matches_return_candidates_without_selecting_or_uploading(self) -> None:
        first_ref = "workspace/_drops/chat-a/flower-a.png"
        second_ref = "workspace/_drops/chat-b/flower-b.png"
        self._write_png(first_ref)
        self._write_png(second_ref)
        self._write_session(
            "chat-a",
            "春天花园",
            [self._attachment("flower-a.png", first_ref, "这是花园里的樱花。")],
        )
        self._write_session(
            "chat-b",
            "春天花园备选",
            [self._attachment("flower-b.png", second_ref, "这是花园里的樱花。")],
        )

        result = find_image_references("花园里的樱花", paths=self.paths, current_session_dir=None)

        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.selected)
        self.assertEqual({item.filename for item in result.candidates}, {"flower-a.png", "flower-b.png"})
        self.assertTrue(all("path" not in item for item in result.to_public_dict()["candidates"]))

    def test_reference_id_selects_exact_candidate_after_user_choice(self) -> None:
        first_ref = "workspace/_drops/chat-a/flower-a.png"
        second_ref = "workspace/_drops/chat-b/flower-b.png"
        self._write_png(first_ref)
        self._write_png(second_ref)
        self._write_session(
            "chat-a",
            "春天花园",
            [self._attachment("flower-a.png", first_ref, "这是花园里的樱花。")],
        )
        self._write_session(
            "chat-b",
            "春天花园备选",
            [self._attachment("flower-b.png", second_ref, "这是花园里的樱花。")],
        )
        candidates = find_image_references("花园里的樱花", paths=self.paths, current_session_dir=None)
        chosen = next(item for item in candidates.candidates if item.filename == "flower-b.png")

        result = find_image_references(
            "",
            paths=self.paths,
            current_session_dir=None,
            reference_id=chosen.reference_id,
        )

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.filename, "flower-b.png")
        self.assertEqual(result.selected.session_id, "chat-b")

    def test_current_attachment_scope_does_not_pick_a_past_conversation_image(self) -> None:
        current_ref = "workspace/_drops/current/current.png"
        past_ref = "workspace/_drops/past/past.png"
        self._write_png(current_ref)
        self._write_png(past_ref)
        current_dir = self._write_session(
            "current", "当前会话", [self._attachment("current.png", current_ref, "当前消息附图")]
        )
        self._write_session(
            "past", "旧会话", [self._attachment("past.png", past_ref, "当前消息附图")]
        )

        result = find_image_references(
            "current_attachment",
            paths=self.paths,
            current_session_dir=current_dir,
        )

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.filename, "current.png")
        self.assertEqual(result.selected.session_id, "current")

    def test_missing_escaped_and_non_supported_references_are_not_candidates(self) -> None:
        self._write_session(
            "bad-refs",
            "无法使用的图片",
            [
                self._attachment("missing.png", "workspace/_drops/missing.png", "找这个场景。"),
                self._attachment("escape.png", "../../outside.png", "找这个场景。"),
                {
                    "role": "user",
                    "content": (
                        "[附件]\n- animation.gif → workspace/_drops/animation.gif "
                        "(1.0 KB, image/gif；图片附件)\n\n找这个场景。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "[附件]\n- host.png → host:desktop/C:/private/host.png "
                        "(1.0 KB, image/png；图片附件)\n\n找这个场景。"
                    ),
                },
            ],
        )

        result = find_image_references("找这个场景", paths=self.paths, current_session_dir=None)

        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.candidates, ())

    def test_terminal_session_image_is_searchable_when_its_message_references_it(self) -> None:
        ref = "workspace/_drops/terminal-chat/reference.png"
        self._write_png(ref)
        self._write_session(
            "terminal-chat",
            "Terminal project image",
            [self._attachment("reference.png", ref, "terminal reference image")],
            harness="terminal",
        )

        result = find_image_references("terminal reference image", paths=self.paths, current_session_dir=None)

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.session_id, "terminal-chat")
        self.assertEqual(result.selected.session_title, "Terminal project image")

    def test_read_authorized_host_attachment_can_be_selected_without_scanning_host_root(self) -> None:
        host_file = self.host_root / "reference.png"
        host_file.write_bytes(b"\x89PNG\r\n\x1a\nhost-image")
        save_host_scope(
            self.paths,
            HostScopeConfig(
                host_roots=[
                    HostRoot(
                        id="photos",
                        path=self.host_root,
                        label="Photos",
                        added_at=utc_now_iso(),
                        read=True,
                        write=False,
                    )
                ]
            ),
        )
        self._write_session(
            "host-chat",
            "Host reference conversation",
            [{
                "role": "user",
                "content": (
                    "[附件]\n- reference.png → host:photos/reference.png "
                    "(1.0 KB, image/png；图片附件)\n\nhost reference photo"
                ),
            }],
        )

        result = find_image_references("host reference photo", paths=self.paths, current_session_dir=None)

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.filename, "reference.png")
        self.assertEqual(result.selected.path, host_file.resolve())
        self.assertNotIn(str(host_file), json.dumps(result.to_public_dict(), ensure_ascii=False))

    def test_stale_reference_id_returns_not_found(self) -> None:
        result = find_image_references(
            "",
            paths=self.paths,
            current_session_dir=None,
            reference_id="ref-does-not-exist",
        )

        self.assertEqual(result.status, "not_found")

