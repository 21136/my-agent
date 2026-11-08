# -*- coding: utf-8 -*-
"""Explicit, self-contained worker batches avoid redundant auto-explore."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from turn_intent import should_spawn_explore


class WorkerBatchExploreGateTests(unittest.TestCase):
    def test_self_contained_worker_batch_skips_only_implicit_explore(self) -> None:
        text = (
            "请调用 run_project_workers 并行提交任务："
            "project-alpha 读取并整理 agent-core/alpha.py；"
            "project-beta 检查并修复 agent-core/beta.py。"
        )
        with patch.dict(os.environ, {"MY_AGENT_AUTO_EXPLORE": "1"}):
            self.assertFalse(should_spawn_explore(text))
            self.assertTrue(should_spawn_explore(text, explicit=True))

    def test_incomplete_worker_dispatch_keeps_automatic_explore(self) -> None:
        text = "请调用 run_project_workers 并行提交项目任务，并检查 agent-core/project.py。"
        with patch.dict(os.environ, {"MY_AGENT_AUTO_EXPLORE": "1"}):
            self.assertTrue(should_spawn_explore(text))

    def test_non_batch_execution_keeps_automatic_explore(self) -> None:
        with patch.dict(os.environ, {"MY_AGENT_AUTO_EXPLORE": "1"}):
            self.assertTrue(should_spawn_explore("帮我实现项目功能并修改 agent-core/project.py"))


if __name__ == "__main__":
    unittest.main()
