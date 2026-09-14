"""Harness-owned verification evidence for runaway project delivery."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from paths import AgentPaths
from project_mode import (
    acceptance_command_to_argv,
    acceptance_script_exists,
    parse_acceptance_command_display,
    parse_acceptance_spec,
    project_dir,
    read_project_artifacts,
    run_acceptance_check,
    utc_now_iso,
)

EVIDENCE_FILENAME = "runaway-verification.json"
EVIDENCE_SCHEMA_VERSION = "0.1"
_ID_RE = re.compile(r"\b(?:AC|V)-\d+(?:-\d+)*\b", re.IGNORECASE)
_OUTPUT_LIMIT = 1600
_ACCEPTANCE_SECTION_HEADER = "## 验收标准"
MISSING_ACCEPTANCE_ERROR = "PROJECT.md 未定义可执行验收命令"
_ACCEPTANCE_SCRIPT_PATTERNS = (
    "**/release-drill.py",
    "**/project_verify.py",
    "**/verify*.py",
    "**/accept*.py",
)


def _find_acceptance_script(root: Path) -> str | None:
    """Pick a project-local python script suitable for PROJECT.md acceptance."""
    candidates: list[Path] = []
    for pattern in _ACCEPTANCE_SCRIPT_PATTERNS:
        candidates.extend(root.glob(pattern))
    acceptance_root = root / ".acceptance"
    if acceptance_root.is_dir():
        candidates.extend(path for path in acceptance_root.rglob("*.py") if path.is_file())
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in candidates:
        key = path.resolve().as_posix()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    ordered.sort(key=lambda item: (len(item.parts), item.as_posix()))
    for path in ordered:
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".plan-agent/"):
            continue
        return rel
    return None


def ensure_env_quality_commands(paths: AgentPaths, project_id: str) -> bool:
    """Bootstrap ENV.md quality.commands from PROJECT acceptance when missing (UI-6046)."""
    from project_env import ENV_FILENAME, ensure_project_env
    from project_quality import parse_quality_commands_from_env_text

    pid = project_id.strip()
    if not pid:
        return False
    root = project_dir(paths, pid)
    env_path = root / ENV_FILENAME
    existing = ""
    if env_path.is_file():
        try:
            existing = env_path.read_text(encoding="utf-8")
        except OSError:
            pass
    if parse_quality_commands_from_env_text(existing):
        return False

    artifacts = read_project_artifacts(paths, pid)
    display = parse_acceptance_command_display(artifacts.get("PROJECT.md", ""))
    if not display:
        return False
    argv = acceptance_command_to_argv(display)
    if not argv:
        return False

    ensure_project_env(
        paths,
        pid,
        quality_commands=[{"id": "acceptance", "cmd": argv}],
    )
    try:
        updated = env_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(parse_quality_commands_from_env_text(updated))


def ensure_project_acceptance_section(paths: AgentPaths, project_id: str) -> bool:
    """Append executable acceptance command to PROJECT.md when missing (R7-31c)."""
    pid = project_id.strip()
    if not pid:
        return False
    root = project_dir(paths, pid)
    project_path = root / "PROJECT.md"
    if not project_path.is_file():
        return False
    original = project_path.read_text(encoding="utf-8")
    if parse_acceptance_command_display(original) is not None:
        return False
    script_rel = _find_acceptance_script(root)
    if not script_rel:
        return False
    command_line = f"- 命令：`python {script_rel}` 期望退出码：0"
    if _ACCEPTANCE_SECTION_HEADER in original:
        updated = original.rstrip() + f"\n{command_line}\n"
    else:
        updated = (
            original.rstrip()
            + f"\n\n{_ACCEPTANCE_SECTION_HEADER}\n\n"
            + "- AC-001 工程基线可验证\n"
            + f"{command_line}\n"
        )
    if updated == original:
        return False
    project_path.write_text(updated, encoding="utf-8")
    return parse_acceptance_spec(updated) is not None


def verification_evidence_path(paths: AgentPaths, project_id: str) -> Path:
    return project_dir(paths, project_id) / ".plan-agent" / EVIDENCE_FILENAME


def _ids(*texts: str) -> list[str]:
    found: set[str] = set()
    for text in texts:
        found.update(match.upper() for match in _ID_RE.findall(text or ""))
    return sorted(found)


def _compact(value: Any) -> str:
    text = str(value or "").strip()
    return text if len(text) <= _OUTPUT_LIMIT else text[:_OUTPUT_LIMIT] + "…"


def _fingerprint(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("evidence_fingerprint", None)
    payload.pop("recorded_at", None)
    payload.pop("evidence_persisted", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _write_evidence(path: Path, record: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(path)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except (NameError, OSError):
            pass
        return False


def run_harness_verification(paths: AgentPaths, project_id: str) -> dict[str, Any]:
    """Execute the authoritative acceptance command and persist its evidence."""
    pid = project_id.strip()
    artifacts = read_project_artifacts(paths, pid)
    project_text = artifacts.get("PROJECT.md", "")
    verify_text = artifacts.get("VERIFY.md", "")
    record: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "project_id": pid,
        "recorded_at": utc_now_iso(),
        "command": None,
        "exit_code": None,
        "expected_exit_code": None,
        "passed": False,
        "verify_ids": _ids(verify_text),
        "ac_ids": _ids(project_text, verify_text),
        "stdout": "",
        "stderr": "",
        "error": "",
    }
    if not record["verify_ids"]:
        record["error"] = "VERIFY.md 未定义 V-* 验证项"
    elif not record["ac_ids"]:
        record["error"] = "项目文档未定义 AC-* 验收项"
    acceptance = parse_acceptance_spec(project_text)
    if record["error"]:
        pass
    elif acceptance is None:
        record["error"] = MISSING_ACCEPTANCE_ERROR
    elif not acceptance_script_exists(paths, pid, acceptance):
        record["command"] = acceptance.display
        record["expected_exit_code"] = acceptance.expected_exit_code
        record["error"] = f"验收脚本不存在：workspace/{pid}/{acceptance.script_rel}"
    else:
        record["command"] = acceptance.display
        record["expected_exit_code"] = acceptance.expected_exit_code
        try:
            result = run_acceptance_check(paths, pid, acceptance)
            record["exit_code"] = result.get("exit_code")
            record["passed"] = bool(result.get("passed"))
            record["stdout"] = _compact(result.get("stdout"))
            record["stderr"] = _compact(result.get("stderr"))
            record["error"] = _compact(result.get("error"))
        except Exception as exc:
            record["error"] = _compact(exc)
    record["evidence_fingerprint"] = _fingerprint(record)
    record["evidence_persisted"] = _write_evidence(
        verification_evidence_path(paths, pid), record
    )
    return record


def load_verification_evidence(paths: AgentPaths, project_id: str) -> dict[str, Any] | None:
    path = verification_evidence_path(paths, project_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("project_id") != project_id:
        return None
    return data


def evidence_passed(record: dict[str, Any] | None, project_id: str) -> bool:
    if not record:
        return False
    fingerprint = str(record.get("evidence_fingerprint") or "")
    return bool(
        record.get("project_id") == project_id
        and record.get("schema_version") == EVIDENCE_SCHEMA_VERSION
        and record.get("passed") is True
        and record.get("evidence_persisted") is True
        and record.get("command")
        and record.get("exit_code") == record.get("expected_exit_code")
        and record.get("verify_ids")
        and record.get("ac_ids")
        and fingerprint
        and _fingerprint(record) == fingerprint
    )
