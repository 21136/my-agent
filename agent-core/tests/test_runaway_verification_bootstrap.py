"""Harness verification bootstrap — ENV quality.commands from PROJECT (UI-6046)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import (
    acceptance_command_to_argv,
    acceptance_script_exists,
    parse_acceptance_spec,
)
from project_quality import parse_quality_commands_from_env_text
from runaway_verification import (
    ensure_env_quality_commands,
    ensure_project_acceptance_section,
)
from tests.isolation_helpers import temporary_agent_paths


class RunawayVerificationBootstrapTests(unittest.TestCase):
    def test_acceptance_command_to_argv_powershell(self) -> None:
        argv = acceptance_command_to_argv("powershell -File verify-prototype.ps1")
        self.assertEqual(argv[0].lower(), "powershell")
        self.assertIn("verify-prototype.ps1", argv[-1])

    def test_parse_acceptance_spec_powershell(self) -> None:
        project_md = (
            "## 验收标准\n\n"
            "- 命令：`powershell -File verify-prototype.ps1` 期望退出码：0\n"
        )
        spec = parse_acceptance_spec(project_md)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertFalse(spec.is_python)
        self.assertEqual(spec.argv[0].lower(), "powershell")

    def test_ensure_env_quality_from_project_acceptance(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "music-bootstrap"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text(
                "## 验收标准\n\n- 命令：`powershell -File verify-prototype.ps1` 期望退出码：0\n",
                encoding="utf-8",
            )
            (root / "ENV.md").write_text(
                'tools:\n  node: ""\nprefer:\n  package_manager: npm\n',
                encoding="utf-8",
            )
            self.assertTrue(ensure_env_quality_commands(paths, pid))
            env_text = (root / "ENV.md").read_text(encoding="utf-8")
            commands = parse_quality_commands_from_env_text(env_text)
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0]["id"], "acceptance")
            self.assertEqual(commands[0]["cmd"][0].lower(), "powershell")

    def test_ensure_env_quality_idempotent(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "music-bootstrap-idem"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "PROJECT.md").write_text(
                "## 验收标准\n\n- 命令：`python verify.py` 期望退出码：0\n",
                encoding="utf-8",
            )
            (root / "ENV.md").write_text(
                'quality:\n  commands:\n    - id: t\n      cmd: ["echo", "ok"]\n',
                encoding="utf-8",
            )
            self.assertFalse(ensure_env_quality_commands(paths, pid))

    def test_ensure_project_acceptance_section_skips_existing_shell_command(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "shell-only"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "verify.py").write_text("print('ok')\n", encoding="utf-8")
            original = (
                "## 验收标准\n\n"
                "- 命令：`powershell -File verify-prototype.ps1` 期望退出码：0\n"
            )
            (root / "PROJECT.md").write_text(original, encoding="utf-8")
            self.assertFalse(ensure_project_acceptance_section(paths, pid))
            self.assertEqual((root / "PROJECT.md").read_text(encoding="utf-8"), original)

    def test_acceptance_script_exists_powershell_file(self) -> None:
        with temporary_agent_paths() as paths:
            pid = "shell-file"
            root = paths.workspace / pid
            root.mkdir(parents=True, exist_ok=True)
            (root / "verify-prototype.ps1").write_text("exit 0\n", encoding="utf-8")
            spec = parse_acceptance_spec(
                "## 验收标准\n\n- 命令：`powershell -File verify-prototype.ps1` 期望退出码：0\n"
            )
            self.assertIsNotNone(spec)
            assert spec is not None
            self.assertTrue(acceptance_script_exists(paths, pid, spec))


if __name__ == "__main__":
    unittest.main()
