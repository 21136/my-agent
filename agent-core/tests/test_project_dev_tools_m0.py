"""Phase 26 M0 — http_request + dev_start wrap (IT-80～IT-82)."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from tests.isolation_helpers import temporary_agent_paths
from tools.executor import ExecutorSession, ToolExecutor, _http_request_needs_confirm
from tools.registry import ToolRegistry


def _load_http_request(main_py: Path):
    spec = importlib.util.spec_from_file_location("http_request_under_test", main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_dev_start(main_py: Path):
    spec = importlib.util.spec_from_file_location("dev_start_under_test", main_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Handler(BaseHTTPRequestHandler):
    body = b'{"ok":true,"pad":"' + (b"x" * 4000) + b'"}'

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format, *args):  # noqa: A003
        return


class HttpRequestTests(unittest.TestCase):
    def test_it80_get_loopback_and_truncate(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/http_request",)) as paths:
            main_py = paths.evolve / "tools" / "common" / "http_request" / "main.py"
            mod = _load_http_request(main_py)
            server = HTTPServer(("127.0.0.1", 0), _Handler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                out = mod.http_request(
                    {
                        "url": f"http://127.0.0.1:{port}/health",
                        "method": "GET",
                        "max_body_chars": 200,
                    }
                )
                self.assertTrue(out.get("ok"), out)
                self.assertEqual(out.get("status_code"), 200)
                self.assertTrue(out.get("loopback"))
                self.assertTrue(out.get("truncated"))
                self.assertLessEqual(len(out.get("body") or ""), 220)
            finally:
                server.shutdown()

    def test_needs_confirm_helpers(self) -> None:
        self.assertFalse(
            _http_request_needs_confirm({"method": "GET", "url": "http://127.0.0.1:8080/x"})
        )
        self.assertFalse(
            _http_request_needs_confirm({"method": "HEAD", "url": "http://localhost:3000/"})
        )
        self.assertTrue(
            _http_request_needs_confirm({"method": "POST", "url": "http://127.0.0.1:8080/x"})
        )
        self.assertTrue(
            _http_request_needs_confirm({"method": "GET", "url": "https://example.com/"})
        )


class HttpRequestConfirmTests(unittest.TestCase):
    def test_it81_confirm_gate(self) -> None:
        with temporary_agent_paths(copy_tool_dirs=("common/http_request",)) as paths:
            registry = ToolRegistry.load(paths)
            self.assertIsNotNone(registry.get_evolved("http_request"))
            confirms: list[str] = []

            def confirm_fn(preview: str, allow_approve_all: bool = False) -> str:
                confirms.append(preview)
                return "n"

            executor = ToolExecutor(
                registry=registry,
                session=ExecutorSession(allowed_evolved={"http_request"}),
                confirm_fn=confirm_fn,
            )
            # Safe loopback GET — no confirm
            # Use a closed port so network fails after skip-confirm; still proves gate.
            safe = executor.run(
                "run_evolved",
                {
                    "tool_name": "http_request",
                    "arguments": {"url": "http://127.0.0.1:9/", "method": "GET"},
                },
            )
            self.assertEqual(confirms, [])
            # May be ok=False due to connection refused — that's fine
            self.assertIsNotNone(safe)

            denied = executor.run(
                "run_evolved",
                {
                    "tool_name": "http_request",
                    "arguments": {"url": "https://example.com/", "method": "GET"},
                },
            )
            self.assertFalse(denied.ok)
            self.assertTrue(confirms)

            confirms.clear()
            denied_post = executor.run(
                "run_evolved",
                {
                    "tool_name": "http_request",
                    "arguments": {
                        "url": "http://127.0.0.1:9/",
                        "method": "POST",
                        "body": "{}",
                    },
                },
            )
            self.assertFalse(denied_post.ok)
            self.assertTrue(confirms)


class DevStartWrapTests(unittest.TestCase):
    def test_it82_dry_run_delegates_to_run_service(self) -> None:
        with temporary_agent_paths(
            copy_tool_dirs=("common/run_service", "coding/dev_start")
        ) as paths:
            # Minimal fake project tree
            proj = paths.workspace / "demoapp"
            (proj / "backend").mkdir(parents=True)
            (proj / "frontend").mkdir(parents=True)

            main_py = paths.evolve / "tools" / "coding" / "dev_start" / "main.py"
            # Patch agent root discovery for copied tool
            mod = _load_dev_start(main_py)
            mod._agent_root = lambda: paths.agent_root  # type: ignore[method-assign]

            out = mod.dev_start(
                {
                    "project_dir": "workspace/demoapp",
                    "dry_run": True,
                    "backend": {"dir": "backend", "command": ["echo", "be"], "port": 18080},
                    "frontend": {"dir": "frontend", "command": ["echo", "fe"], "port": 13000},
                }
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("dry_run"))
            self.assertEqual(out.get("via"), "run_service")
            planned = out.get("planned") or []
            self.assertEqual(len(planned), 2)
            names = {p["name"] for p in planned}
            self.assertIn("demoapp-backend", names)
            self.assertIn("demoapp-frontend", names)

            registry = ToolRegistry.load(paths)
            self.assertIsNotNone(registry.get_evolved("run_service"))
            self.assertIsNotNone(registry.get_evolved("dev_start"))

            # Catalog: run.md must prefer run_service wording (checked against live root)
            live_run = Path(__file__).resolve().parents[2] / "evolve" / "tool-catalog" / "buckets" / "run.md"
            text = live_run.read_text(encoding="utf-8")
            self.assertIn("`run_service`", text)
            self.assertIn("`http_request`", text)
            # Primary path called out; dev_start described as wrap if present
            self.assertIn("run_service", text.lower())


if __name__ == "__main__":
    unittest.main()
