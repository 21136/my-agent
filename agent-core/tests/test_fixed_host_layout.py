from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixed_host import parse_fixed_host_layout, read_fixed_host_layout
from paths import AgentPaths, PathDeniedForWriteError, PathOutOfBoundsError
import server


SANDBOX = Path(__file__).resolve().parents[2]
ARTIFACTS = SANDBOX / "artifacts" / "fixed-host-layout-tests"
PROTOCOL = "my-agent-fixed-layout-v1"


def canonical(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


class FixedLayoutFixtures(unittest.TestCase):
    def setUp(self) -> None:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="case-", dir=ARTIFACTS)
        self.root = Path(self.temp.name)
        self.code = self.root / "frozen-code"
        self.instance = self.root / "instance"
        (self.code / "evolve").mkdir(parents=True)
        (self.code / "evolve" / "_index.core.toml").write_text("# fixture\n", encoding="utf-8")
        self.instance.mkdir()
        for child in ("data", "workspace", "profile"):
            (self.instance / child).mkdir()
        self.marker = {
            "format": 1,
            "mode": "launcher_fixture_v1",
            "code_root": str(self.code.resolve()),
        }
        (self.instance / ".fixed-host-instance.json").write_bytes(canonical(self.marker))
        self.python_root = self.root / "python"
        self.python_base_root = self.root / "python-base"
        self.python_root.mkdir()
        self.python_base_root.mkdir()
        self.layout = {
            "protocol": PROTOCOL,
            "code_root": str(self.code.resolve()),
            "instance_root": str(self.instance.resolve()),
            "runtime": {
                "python_executable": str((self.python_root / "python.exe").resolve()),
                "python_root": str(self.python_root.resolve()),
                "python_base_root": str(self.python_base_root.resolve()),
            },
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_paths_keep_mutable_roots_in_instance_and_evolve_in_code(self) -> None:
        paths = AgentPaths.from_fixed_layout(self.code, self.instance)
        self.assertEqual(paths.agent_root, self.instance.resolve())
        self.assertEqual(paths.data, self.instance / "data")
        self.assertEqual(paths.workspace, self.instance / "workspace")
        self.assertEqual(paths.evolve, self.code / "evolve")
        self.assertEqual(paths.resolve_under_agent("evolve/_index.core.toml"), self.code / "evolve" / "_index.core.toml")
        self.assertEqual(paths.resolve_under_agent("data/state.json"), self.instance / "data" / "state.json")
        self.assertEqual(paths.resolve_under_agent("profile/settings.json"), self.instance / "profile" / "settings.json")
        self.assertEqual(paths.to_agent_relative(self.instance / "workspace" / "notes.md"), "workspace/notes.md")
        self.assertEqual(paths.to_agent_relative(self.instance / "data" / "state.json"), "data/state.json")
        self.assertEqual(paths.to_agent_relative(self.code / "evolve" / "_index.core.toml"), "evolve/_index.core.toml")
        self.assertEqual(paths.to_agent_or_host_path(self.code / "evolve" / "_index.core.toml"), "evolve/_index.core.toml")

    def test_fixed_layout_rejects_writes_to_evolve_by_logical_and_absolute_path(self) -> None:
        paths = AgentPaths.from_fixed_layout(self.code, self.instance)
        for target in ("evolve/new.py", str(self.code / "evolve" / "new.py")):
            with self.subTest(target=target), self.assertRaises((PathDeniedForWriteError, PathOutOfBoundsError)):
                paths.resolve_under_agent_for_write(target)

    def test_fixed_layout_allows_instance_data_and_workspace_writes(self) -> None:
        paths = AgentPaths.from_fixed_layout(self.code, self.instance)
        self.assertEqual(paths.resolve_under_agent_for_write("data/state.json"), self.instance / "data" / "state.json")
        self.assertEqual(paths.resolve_under_workspace("notes.txt"), self.instance / "workspace" / "notes.txt")

    def test_fixed_layout_rejects_missing_or_wrong_instance_marker(self) -> None:
        marker_path = self.instance / ".fixed-host-instance.json"
        marker_path.unlink()
        with self.assertRaises(ValueError):
            AgentPaths.from_fixed_layout(self.code, self.instance)
        marker_path.write_text(json.dumps({**self.marker, "extra": True}), encoding="utf-8")
        with self.assertRaises(ValueError):
            AgentPaths.from_fixed_layout(self.code, self.instance)

    def test_fixed_layout_rejects_overlapping_roots_and_linked_roots(self) -> None:
        with self.assertRaises(ValueError):
            AgentPaths.from_fixed_layout(self.code, self.code / "instance")
        with self.assertRaises(ValueError):
            AgentPaths.from_fixed_layout(self.code / ".." / "frozen-code", self.instance)
        linked = self.root / "linked-instance"
        try:
            linked.symlink_to(self.instance, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink creation is unavailable")
        with self.assertRaises(ValueError):
            AgentPaths.from_fixed_layout(self.code, linked)

    def test_fixed_layout_requires_precreated_child_directories(self) -> None:
        for child in ("evolve", "data", "workspace", "profile"):
            with self.subTest(child=child):
                root = self.code if child == "evolve" else self.instance
                child_path = root / child if child != "evolve" else root / "evolve"
                original = child_path
                original.rename(original.with_name(original.name + "_saved"))
                try:
                    with self.assertRaises(ValueError):
                        AgentPaths.from_fixed_layout(self.code, self.instance)
                finally:
                    original.with_name(original.name + "_saved").rename(original)
        bad_child = self.instance / "profile"
        bad_child.rmdir()
        bad_child.write_text("not a directory", encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                AgentPaths.from_fixed_layout(self.code, self.instance)
        finally:
            bad_child.unlink()
            bad_child.mkdir()

    def test_fixed_layout_rejects_linked_child_when_available(self) -> None:
        children = (
            (self.code, "evolve"),
            (self.instance, "data"),
            (self.instance, "workspace"),
            (self.instance, "profile"),
        )
        for parent, name in children:
            child = parent / name
            saved = parent / f"{name}_saved"
            target = self.root / f"{name}_target"
            child.rename(saved)
            target.mkdir()
            try:
                child.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                target.rmdir()
                saved.rename(child)
                self.skipTest("directory symlink creation is unavailable")
            try:
                with self.subTest(child=str(child)), self.assertRaises(ValueError):
                    AgentPaths.from_fixed_layout(self.code, self.instance)
            finally:
                child.unlink()
                saved.rename(child)
                target.rmdir()

    def test_ordinary_from_root_layout_remains_unchanged(self) -> None:
        paths = AgentPaths.from_root(self.code)
        self.assertEqual(paths.agent_root, self.code.resolve())
        self.assertEqual(paths.evolve, self.code / "evolve")
        self.assertEqual(paths.resolve_under_agent("evolve/_index.core.toml"), self.code / "evolve" / "_index.core.toml")

    def test_bootstrap_accepts_exact_canonical_single_frame(self) -> None:
        raw = canonical(self.layout)
        self.assertEqual(parse_fixed_host_layout(raw), self.layout)
        self.assertEqual(read_fixed_host_layout(io.BytesIO(raw)), self.layout)

    def test_bootstrap_matches_parent_sorted_key_frame_literal(self) -> None:
        raw = b'{"code_root":"C:/fixed/code","instance_root":"C:/fixed/instance","protocol":"my-agent-fixed-layout-v1","runtime":{"python_base_root":"C:/python-base","python_executable":"C:/python/python.exe","python_root":"C:/python"}}\n'
        expected = {
            "code_root": "C:/fixed/code",
            "instance_root": "C:/fixed/instance",
            "protocol": PROTOCOL,
            "runtime": {
                "python_base_root": "C:/python-base",
                "python_executable": "C:/python/python.exe",
                "python_root": "C:/python",
            },
        }
        self.assertEqual(parse_fixed_host_layout(raw), expected)
        unsorted = b'{"protocol":"my-agent-fixed-layout-v1","instance_root":"C:/fixed/instance","code_root":"C:/fixed/code","runtime":{"python_root":"C:/python","python_executable":"C:/python/python.exe","python_base_root":"C:/python-base"}}\n'
        with self.assertRaises(ValueError):
            parse_fixed_host_layout(unsorted)

    def test_bootstrap_rejects_duplicate_unknown_noncanonical_and_extra_frames(self) -> None:
        valid = canonical(self.layout)
        bad_frames = (
            valid + b"{}\n",
            valid.rstrip(b"\n"),
            valid.replace(b'"protocol":', b'"protocol":"other","protocol":', 1),
            canonical({**self.layout, "extra": True}),
            json.dumps(self.layout).encode() + b"\n",
            canonical({**self.layout, "code_root": "relative-code"}),
            canonical({**self.layout, "runtime": {**self.layout["runtime"], "extra": "x"}}),
            b"x" * 4097,
        )
        for raw in bad_frames:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_fixed_host_layout(raw)
        with self.assertRaises(ValueError):
            read_fixed_host_layout(io.BytesIO(valid + b"{}\n"))

    def test_ordinary_server_cli_compatibility_and_fixed_entry_guard(self) -> None:
        args = server.build_parser().parse_args(["--host", "127.0.0.1", "--port", "0"])
        self.assertEqual((args.host, args.port), ("127.0.0.1", 0))
        self.assertTrue(server.build_parser().parse_args(["--fixed-host-layout-stdin"]).fixed_host_layout_stdin)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            server.build_parser().parse_args(["--fixed-host-layout-stdin", "--evolution-bootstrap-stdin"])
        with contextlib.redirect_stdout(io.StringIO()) as output, patch.object(server, "run_server") as run_server:
            self.assertEqual(server.main(["--fixed-host-layout-stdin"]), 4)
        self.assertIn("fixed_host_entry_required", output.getvalue())
        run_server.assert_not_called()

    def test_fixed_host_e0_blocks_chat_and_evolve_mutations_before_dispatch(self) -> None:
        paths = AgentPaths.from_fixed_layout(self.code, self.instance)
        handler = object.__new__(server.WsSessionHandler)
        handler.paths = paths
        events: list[dict[str, object]] = []
        bridge = server.WsBridge(emit=events.append, paths=paths)
        messages = (
            {"type": "proposal.accept", "proposal_id": "p-1"},
            {"type": "proposal.reject", "proposal_id": "p-1"},
            {"type": "companion.sync"},
            {"type": "user.message", "text": "hello"},
            {"type": "command", "name": "proposals accept p-1"},
        )
        for message in messages:
            with self.subTest(message_type=message["type"]):
                events.clear()
                with patch.object(server, "log_sidecar_ws_error"):
                    asyncio.run(handler._dispatch(message, object(), bridge))
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].get("type"), "error")
                self.assertIn("fixed Host E0", str(events[0].get("message")))

    def test_run_server_uses_fixed_layout_before_any_lock_or_listener(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), patch.object(server, "configure_sidecar_logging") as configure, patch.object(
            AgentPaths, "from_fixed_layout", wraps=AgentPaths.from_fixed_layout
        ) as construct, patch.object(
            AgentPaths, "discover", side_effect=AssertionError("fixed mode must not discover roots")
        ):
            result = server.asyncio.run(
                server.run_server(
                    "127.0.0.1", 0, takeover=True, fixed_host_layout=self.layout
                )
            )
        self.assertEqual(result, 2)
        construct.assert_called_once_with(self.layout["code_root"], self.layout["instance_root"])
        configure.assert_called_once()


if __name__ == "__main__":
    unittest.main()


