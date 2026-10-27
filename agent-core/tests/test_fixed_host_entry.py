from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SANDBOX = Path(__file__).resolve().parents[2]
ARTIFACTS = SANDBOX / "artifacts" / "fixed-host-layout-tests"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

import fixed_host
import fixed_host_entry as entry
from fixed_host_entry import (
    RuntimeContext,
    RuntimeInfo,
    add_registered_import_paths,
    parse_private_layout_frame,
    read_private_layout_frame,
    validate_module_snapshot,
    validate_runtime_layout,
)


class FixedHostEntryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="entry-", dir=ARTIFACTS)
        self.root = Path(self.temp.name)
        self.code = self.root / "frozen-code"
        self.instance = self.root / "instance"
        (self.code / "agent-core").mkdir(parents=True)
        evolve = self.code / "evolve"
        evolve.mkdir()
        (evolve / "_index.core.toml").write_text("# fixture\n", encoding="utf-8")
        (self.code / "agent-core" / "server.py").write_text("# fixture\n", encoding="utf-8")
        self.entry_file = self.code / "agent-core" / "fixed_host_entry.py"
        self.entry_file.write_text("# fixture entry\n", encoding="utf-8")
        self.instance.mkdir()
        for name in ("data", "workspace", "profile"):
            (self.instance / name).mkdir()
        (self.instance / ".fixed-host-instance.json").write_text(
            json.dumps({"format": 1, "mode": "launcher_fixture_v1", "code_root": str(self.code.resolve())}),
            encoding="utf-8",
        )
        self.python_root = self.root / "python"
        self.python_base_root = self.root / "python-base"
        self.python_root.mkdir()
        self.python_base_root.mkdir()
        self.layout = {
            "protocol": "my-agent-fixed-layout-v1",
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
    def runtime_info(self) -> RuntimeInfo:
        executable = self.python_root / "Scripts" / "python.exe"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"fixture executable")
        self.layout["runtime"]["python_executable"] = str(executable.resolve())
        base_lib = self.python_base_root / "Lib"
        base_lib.mkdir(exist_ok=True)
        site_packages = self.python_root / "Lib" / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)
        return {
            "executable": str(executable.resolve()),
            "prefix": str(self.python_root.resolve()),
            "base_prefix": str(self.python_base_root.resolve()),
            "sys_path": [str(base_lib.resolve()), str(self.python_base_root / "python314.zip")],
            "version_info": (3, 14),
            "isolated": 1,
            "no_site": 1,
            "dont_write_bytecode": True,
        }


    def canonical_frame(self) -> bytes:
        return json.dumps(
            self.layout,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"

    def invoke_main(
        self,
        raw: bytes,
        runtime_info: RuntimeInfo,
        entry_file: Path,
        *,
        loader_side_effect=None,
    ):
        original_sys_path = list(sys.path)
        stdin = SimpleNamespace(buffer=io.BytesIO(raw))
        loader = patch.object(
            entry,
            "_load_and_run_server",
            side_effect=loader_side_effect or AssertionError("server loader must not run"),
        )
        try:
            with (
                patch.object(entry.sys, "stdin", stdin),
                patch.object(entry, "_runtime_info", return_value=runtime_info),
                patch.object(entry, "__file__", str(entry_file)),
                loader as load_and_run,
                patch.object(entry.importlib, "import_module", side_effect=AssertionError("server import forbidden in this test")) as import_module,
                patch.object(entry.asyncio, "run", side_effect=AssertionError("async server forbidden in this test")) as asyncio_run,
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                result = entry.main(["--port", "0"])
                return result, load_and_run, import_module, asyncio_run, stdout.getvalue()
        finally:
            sys.path[:] = original_sys_path

    def test_shared_and_cold_parsers_accept_the_same_sorted_frame(self) -> None:
        raw = self.canonical_frame()
        self.assertEqual(fixed_host.parse_fixed_host_layout(raw), self.layout)
        self.assertEqual(parse_private_layout_frame(raw), self.layout)
        self.assertEqual(fixed_host.read_fixed_host_layout(io.BytesIO(raw)), self.layout)
        read_raw, read_layout = read_private_layout_frame(io.BytesIO(raw))
        self.assertEqual(read_raw, raw)
        self.assertEqual(read_layout, self.layout)

    def test_both_parsers_reject_duplicate_noncanonical_and_extra_frames(self) -> None:
        canonical = self.canonical_frame()
        duplicate = canonical.replace(
            b'"protocol":', b'"protocol":"wrong","protocol":', 1
        )
        noncanonical = json.dumps(self.layout, ensure_ascii=False).encode("utf-8") + b"\n"
        extra_frame = canonical + b"{}\n"
        for raw in (duplicate, noncanonical, extra_frame):
            with self.subTest(frame=raw):
                with self.assertRaises(ValueError):
                    fixed_host.parse_fixed_host_layout(raw)
                with self.assertRaises(ValueError):
                    parse_private_layout_frame(raw)
                with self.assertRaises(ValueError):
                    fixed_host.read_fixed_host_layout(io.BytesIO(raw))
                with self.assertRaises(ValueError):
                    read_private_layout_frame(io.BytesIO(raw))

    def test_main_invalid_frame_never_reaches_server_loader(self) -> None:
        info = self.runtime_info()
        result, loader, import_module, asyncio_run, stdout = self.invoke_main(
            self.canonical_frame() + b"{}\n", info, self.entry_file
        )
        self.assertEqual(result, 4)
        loader.assert_not_called()
        import_module.assert_not_called()
        asyncio_run.assert_not_called()
        self.assertEqual(json.loads(stdout), {"ready": False, "error": "fixed_host_bootstrap_invalid"})

    def test_main_wrong_entry_never_reaches_server_loader(self) -> None:
        info = self.runtime_info()
        wrong_entry = self.root / "wrong-entry.py"
        wrong_entry.write_text("# wrong entry\n", encoding="utf-8")
        result, loader, import_module, asyncio_run, stdout = self.invoke_main(
            self.canonical_frame(), info, wrong_entry
        )
        self.assertEqual(result, 4)
        loader.assert_not_called()
        import_module.assert_not_called()
        asyncio_run.assert_not_called()
        self.assertEqual(json.loads(stdout), {"ready": False, "error": "fixed_host_bootstrap_invalid"})

    def test_main_wrong_runtime_flags_never_reaches_server_loader(self) -> None:
        info = self.runtime_info()
        info["no_site"] = 0
        result, loader, import_module, asyncio_run, stdout = self.invoke_main(
            self.canonical_frame(), info, self.entry_file
        )
        self.assertEqual(result, 4)
        loader.assert_not_called()
        import_module.assert_not_called()
        asyncio_run.assert_not_called()
        self.assertEqual(json.loads(stdout), {"ready": False, "error": "fixed_host_bootstrap_invalid"})

    def test_valid_main_passes_layout_and_context_without_loading_server(self) -> None:
        info = self.runtime_info()
        captured: dict[str, object] = {}

        def fake_loader(layout, host, port, context):
            captured.update(layout=layout, host=host, port=port, context=context)
            self.assertIn(str(context.agent_core), sys.path)
            return 37

        original_sys_path = list(sys.path)
        result, loader, import_module, asyncio_run, stdout = self.invoke_main(
            self.canonical_frame(), info, self.entry_file, loader_side_effect=fake_loader
        )
        self.assertEqual(result, 37)
        loader.assert_called_once()
        self.assertEqual(captured["layout"], self.layout)
        self.assertEqual(captured["host"], "127.0.0.1")
        self.assertEqual(captured["port"], 0)
        self.assertIsInstance(captured["context"], RuntimeContext)
        import_module.assert_not_called()
        asyncio_run.assert_not_called()
        self.assertEqual(stdout, "")
        self.assertEqual(sys.path, original_sys_path)

    def test_runtime_accepts_registered_roots_and_missing_base_zip_lexically(self) -> None:
        context = validate_runtime_layout(self.layout, self.runtime_info())
        self.assertEqual(context.python_root, self.python_root.resolve())
        self.assertEqual(context.python_base_root, self.python_base_root.resolve())
        self.assertEqual(context.site_packages, (self.python_root / "Lib" / "site-packages").resolve())
        self.assertIn(self.python_base_root / "python314.zip", context.allowed_archives)
        # -S may expose base sys.prefix on Python versions where venv prefixing
        # still depends on site initialization.
        base_prefix_context = validate_runtime_layout(
            self.layout, {**self.runtime_info(), "prefix": str(self.python_base_root.resolve())}
        )
        self.assertEqual(base_prefix_context.python_root, self.python_root.resolve())

    def test_runtime_rejects_external_sys_path_and_executable_mismatch(self) -> None:
        info = self.runtime_info()
        with self.assertRaises(ValueError):
            validate_runtime_layout(self.layout, {**info, "sys_path": [*info["sys_path"], str(self.root)]})
        wrong_executable = self.python_base_root / "python.exe"
        wrong_executable.write_bytes(b"fixture base executable")
        with self.assertRaises(ValueError):
            validate_runtime_layout(self.layout, {**info, "executable": str(wrong_executable.resolve())})

    def test_runtime_rejects_overlapping_roots_and_unregistered_prefix(self) -> None:
        info = self.runtime_info()
        overlapping = {**self.layout, "runtime": {**self.layout["runtime"], "python_root": str(self.code)}}
        with self.assertRaises(ValueError):
            validate_runtime_layout(overlapping, info)
        with self.assertRaises(ValueError):
            validate_runtime_layout(self.layout, {**info, "prefix": str(self.root)})

    def test_registered_site_packages_are_added_without_processing_pth(self) -> None:
        import builtins

        context = validate_runtime_layout(self.layout, self.runtime_info())
        pth = context.site_packages / "startup-probe.pth"
        pth.write_text("import builtins; builtins._fixed_host_pth_probe = True\n", encoding="utf-8")
        self.addCleanup(lambda: delattr(builtins, "_fixed_host_pth_probe") if hasattr(builtins, "_fixed_host_pth_probe") else None)
        import_path = [str(self.python_base_root / "Lib")]
        add_registered_import_paths(import_path, context)
        self.assertEqual(import_path[0], str(context.agent_core))
        self.assertEqual(import_path[-1], str(context.site_packages))
        self.assertFalse(hasattr(builtins, "_fixed_host_pth_probe"))

    def test_module_snapshot_allows_registered_and_frozen_and_rejects_external(self) -> None:
        context = validate_runtime_layout(self.layout, self.runtime_info())
        site_module = context.site_packages / "pkg.py"
        site_module.write_text("# fixture\n", encoding="utf-8")
        validate_module_snapshot(
            [("json", "frozen"), ("server", str(self.code / "agent-core" / "server.py")),
             ("pkg", str(site_module))],
            roots=context.allowed_roots,
            archives=context.allowed_archives,
        )
        archive_origin = context.allowed_archives[0] / "encodings" / "__init__.py"
        validate_module_snapshot(
            [("zipped_stdlib", str(archive_origin))],
            roots=context.allowed_roots,
            archives=context.allowed_archives,
        )
        with self.assertRaises(ValueError):
            validate_module_snapshot(
                [("unsafe", str(self.root / "outside.py"))],
                roots=context.allowed_roots,
                archives=context.allowed_archives,
            )


if __name__ == "__main__":
    unittest.main()

