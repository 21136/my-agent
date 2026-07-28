"""Confirm pipeline hardening (CONFIRM-PIPELINE C1–C7, BUG-008)."""

from __future__ import annotations

import base64
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from server import WsBridge, confirm_timeout_sec
from tools.executor import ToolExecutor, _write_evolve_content_guard
from tools.registry import ToolRegistry
from tools.schema import ToolErrorCode


class ConfirmPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = AgentPaths.discover()
        self.events: list[dict[str, Any]] = []

    def _bridge(self) -> WsBridge:
        return WsBridge(emit=self.events.append, paths=self.paths)

    def test_stale_deliver_confirm_rejected(self) -> None:
        bridge = self._bridge()
        result: list[str] = []

        def waiter() -> None:
            result.append(bridge.confirm_fn("preview", False))

        worker = threading.Thread(target=waiter, daemon=True)
        worker.start()
        for _ in range(100):
            if bridge._pending_confirm_id:
                break
            time.sleep(0.01)
        pending = bridge._pending_confirm_id
        self.assertIsNotNone(pending)
        self.assertFalse(bridge.deliver_confirm("wrong-id", "y"))
        self.assertTrue(bridge.deliver_confirm(pending or "", "y"))
        worker.join(timeout=2)
        self.assertEqual(result, ["y"])
        choices = [e.get("choice") for e in self.events if e.get("type") == "confirm.done"]
        self.assertIn("stale", choices)
        self.assertIn("y", choices)

    def test_wrong_request_id_emits_confirm_done_without_spin(self) -> None:
        """T-1804-01 / BUG-008 C1: stale deliver_confirm must not block confirm_fn."""
        bridge = self._bridge()
        wrong_id = "not-the-pending-id"
        result: list[str] = []

        worker = threading.Thread(
            target=lambda: result.append(bridge.confirm_fn("preview", False)),
            daemon=True,
        )
        worker.start()
        for _ in range(100):
            if bridge._pending_confirm_id:
                break
            time.sleep(0.01)
        pending = bridge._pending_confirm_id
        self.assertIsNotNone(pending)
        self.assertNotEqual(wrong_id, pending)

        before = len(self.events)
        self.assertFalse(bridge.deliver_confirm(wrong_id, "y"))

        stale_done = [
            e
            for e in self.events[before:]
            if e.get("type") == "confirm.done" and e.get("request_id") == wrong_id
        ]
        self.assertEqual(len(stale_done), 1)
        self.assertEqual(stale_done[0].get("choice"), "stale")
        self.assertTrue(
            any(
                e.get("type") == "notice" and "过期" in e.get("text", "")
                for e in self.events[before:]
            )
        )

        # Wrong id must not unblock confirm_fn or clear pending (no spin-to-complete).
        time.sleep(0.15)
        self.assertFalse(result, "confirm_fn returned early on stale request_id")
        self.assertTrue(worker.is_alive())
        self.assertEqual(bridge._pending_confirm_id, pending)

        self.assertTrue(bridge.deliver_confirm(pending or "", "y"))
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ["y"])

    def test_stale_card_returns_notice(self) -> None:
        """T-1804-03 / BUG-008: old confirm cards must emit user-visible notice."""
        bridge = self._bridge()
        stale_id = "stale-card-id"
        result: list[str] = []

        worker = threading.Thread(
            target=lambda: result.append(bridge.confirm_fn("preview", False)),
            daemon=True,
        )
        worker.start()
        pending: str | None = None
        for _ in range(100):
            if bridge._pending_confirm_id:
                pending = bridge._pending_confirm_id
                break
            time.sleep(0.01)
        self.assertIsNotNone(pending)
        self.assertNotEqual(stale_id, pending)

        before_ingress = len(self.events)
        self.assertFalse(bridge.deliver_confirm(stale_id, "y"))
        ingress_events = self.events[before_ingress:]
        ingress_notices = [e for e in ingress_events if e.get("type") == "notice"]
        self.assertEqual(len(ingress_notices), 1)
        self.assertIn("请点最新一张工具确认卡", ingress_notices[0].get("text", ""))
        self.assertTrue(
            any(
                e.get("type") == "confirm.done"
                and e.get("request_id") == stale_id
                and e.get("choice") == "stale"
                for e in ingress_events
            )
        )

        # Queue defense: stale id already waiting is discarded with a distinct notice.
        orphan_id = "orphan-queue-id"
        bridge._confirm_queue.put((orphan_id, "y"))
        time.sleep(0.05)
        queue_notices = [
            e
            for e in self.events[before_ingress:]
            if e.get("type") == "notice" and "request_id 不匹配" in e.get("text", "")
        ]
        self.assertEqual(len(queue_notices), 1)
        self.assertTrue(
            any(
                e.get("type") == "confirm.done"
                and e.get("request_id") == orphan_id
                and e.get("choice") == "stale"
                for e in self.events[before_ingress:]
            )
        )

        self.assertTrue(bridge.deliver_confirm(pending or "", "n"))
        worker.join(timeout=2)
        self.assertEqual(result, ["n"])

    def test_orphan_queue_entry_discarded(self) -> None:
        bridge = self._bridge()
        bridge._confirm_queue.put(("orphan", "y"))
        result: list[str] = []

        def waiter() -> None:
            result.append(bridge.confirm_fn("preview", False))

        worker = threading.Thread(target=waiter, daemon=True)
        worker.start()
        for _ in range(100):
            if bridge._pending_confirm_id:
                break
            time.sleep(0.01)
        pending = bridge._pending_confirm_id
        self.assertIsNotNone(pending)
        time.sleep(0.05)
        self.assertTrue(bridge.deliver_confirm(pending or "", "n"))
        worker.join(timeout=2)
        self.assertEqual(result, ["n"])
        self.assertTrue(
            any(e.get("type") == "confirm.done" and e.get("choice") == "stale" for e in self.events)
        )

    def test_confirm_timeout_emits_done(self) -> None:
        bridge = WsBridge(emit=self.events.append, paths=self.paths, confirm_timeout=0.01)
        choice = bridge.confirm_fn("timeout preview", False)
        self.assertEqual(choice, "n")
        timeout_done = [
            e for e in self.events if e.get("type") == "confirm.done" and e.get("choice") == "timeout"
        ]
        self.assertEqual(len(timeout_done), 1)
        self.assertIsNone(bridge._pending_confirm_id)
        self.assertTrue(any(e.get("type") == "notice" and "超时" in e.get("text", "") for e in self.events))

    def test_confirm_timeout_sec_env_emits_done(self) -> None:
        """T-1804-02 / BUG-008b C2: CONFIRM_TIMEOUT_SEC drives confirm.done on timeout."""
        with patch.dict(os.environ, {"CONFIRM_TIMEOUT_SEC": "0.12"}, clear=False):
            self.assertAlmostEqual(confirm_timeout_sec(), 0.12, places=2)
            events: list[dict[str, Any]] = []
            bridge = WsBridge(emit=events.append, paths=self.paths)
            result: list[str] = []

            worker = threading.Thread(
                target=lambda: result.append(bridge.confirm_fn("env timeout preview", False)),
                daemon=True,
            )
            worker.start()
            request_id: str | None = None
            for _ in range(100):
                if bridge._pending_confirm_id:
                    request_id = bridge._pending_confirm_id
                    break
                time.sleep(0.01)
            self.assertIsNotNone(request_id)

            worker.join(timeout=2)
            self.assertEqual(result, ["n"])
            self.assertIsNone(bridge._pending_confirm_id)

            timeout_done = [
                e
                for e in events
                if e.get("type") == "confirm.done" and e.get("request_id") == request_id
            ]
            self.assertEqual(len(timeout_done), 1)
            self.assertEqual(timeout_done[0].get("choice"), "timeout")
            self.assertTrue(
                any(e.get("type") == "notice" and "超时" in e.get("text", "") for e in events)
            )

    def test_invalid_base64_rejected_before_confirm(self) -> None:
        bad = "abc"  # len 3 → not multiple of 4
        err = _write_evolve_content_guard(
            "evolve/tools/coding/demo/main.py",
            {"path": "evolve/tools/coding/demo/main.py", "content_base64": bad},
            {"tool_name": "write_evolve", "content_base64": bad, "arguments": {}},
        )
        self.assertIsNotNone(err)
        assert err is not None
        self.assertFalse(err.ok)
        self.assertEqual(err.error.code if err.error else "", ToolErrorCode.VALIDATION_ERROR)
        self.assertIn("content_base64 decode failed", err.error.message if err.error else "")

    def test_valid_base64_passes_guard(self) -> None:
        body = b'print("hi")\n'
        good = base64.b64encode(body).decode("ascii")
        err = _write_evolve_content_guard(
            "evolve/tools/coding/demo/main.py",
            {"path": "evolve/tools/coding/demo/main.py", "content_base64": good},
            {"tool_name": "write_evolve", "content_base64": good, "arguments": {}},
        )
        self.assertIsNone(err)

    def test_tool_end_emitted_even_on_execute_failure(self) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        registry = ToolRegistry.load(self.paths)
        executor = ToolExecutor(
            registry=registry,
            confirm_fn=lambda *_a, **_k: "y",
            on_event=lambda et, payload: events.append((et, payload)),
        )

        def boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("boom")

        executor._execute_builtin = boom  # type: ignore[method-assign]
        result = executor.run("list_dir", {"path": "."})
        self.assertFalse(result.ok)
        types = [et for et, _ in events]
        self.assertIn("tool.start", types)
        self.assertIn("tool.end", types)
        end = next(p for et, p in events if et == "tool.end")
        self.assertFalse(end["ok"])


if __name__ == "__main__":
    unittest.main()
