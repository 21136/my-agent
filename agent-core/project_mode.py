"""Workspace project mode: roots, artifacts, safety guards (PROJECT-MODE T-1102–T-1107)."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_manifest import (
    DEFAULT_PROJECT_TEMPLATE,
    LIGHT_SCAFFOLD_ARTIFACTS,
    STANDARD_ARTIFACTS,
    is_light_project_template,
    normalize_project_template,
    project_template_of,
)

ShellId = Literal["grow", "daily", "govern", "project"]
PlanStatus = Literal["", "draft", "confirmed", "plan_dirty"]
ProjectWorkflowStage = Literal[
    "requirements",
    "documentation",
    "design",
    "implementation",
    "verification",
    "release",
]
ProjectDeliveryProfile = Literal["solo", "ritual"]
DEFAULT_PROJECT_DELIVERY_PROFILE: ProjectDeliveryProfile = "solo"
VALID_PROJECT_DELIVERY_PROFILES = frozenset({"solo", "ritual"})
VALID_SHELLS = frozenset({"grow", "daily", "govern", "project"})
VALID_PLAN_STATUSES = frozenset({"", "draft", "confirmed", "plan_dirty"})

STANDARD_PROJECT_ARTIFACTS = STANDARD_ARTIFACTS
PROJECT_ARTIFACTS = frozenset((*STANDARD_PROJECT_ARTIFACTS, "MAP.md", "ENV.md"))
PLAN_DOMAIN_FILES = frozenset((*STANDARD_PROJECT_ARTIFACTS, "MAP.md", "ENV.md"))
CORE_PROJECT_ARTIFACTS = ("PROJECT.md", "DESIGN.md", "VERIFY.md")  # TASKS.md retired
_TEMPLATE_DIRNAME = "_template"
# TASKS.md is no longer generated (task subsystem removed); legacy files are left alone.
_TEMPLATE_FILES = tuple(name for name in (*STANDARD_PROJECT_ARTIFACTS, "MAP.md") if name != "TASKS.md")
_PROJECT_MARKERS = ("PROJECT.md", "TASKS.md", ".plan-agent/manifest.json")
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_VERIFY_ID_INLINE_RE = re.compile(r"\bV-\d+(?:-\d+)*\b", re.IGNORECASE)
_ACCEPT_CMD_RE = re.compile(r"命令[：:]\s*`([^`]+)`", re.IGNORECASE)
_ACCEPT_EXIT_RE = re.compile(r"退出码\s*(\d+)", re.IGNORECASE)
_PYTHON_SCRIPT_RE = re.compile(r"python(?:3)?\s+([^\s`]+\.py)", re.IGNORECASE)

_CODING_TOOLS = frozenset(
    {"run_python", "run_command", "run_tests", "run_demo", "patch_file"}
)
_WRITE_TOOLS = frozenset({"write_text", "append_text", "copy_move", "move_to_trash"})
# verification / release：只读验收执行，不算「写业务代码」（Phase 59 · UI-5966）
_VERIFY_STAGE_EXEC_TOOLS = frozenset(
    {
        "run_command",
        "run_tests",
        "run_python",
        "run_demo",
        "run_project_tests",
        "run_quality",
    }
)


class ProjectModeError(Exception):
    """Invalid project id or state."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}



EXECUTION_STAGES = ("requirements", "documentation", "design", "implementation", "verification", "release")


def classify_stage_documents(
    manifest: dict[str, Any] | None,
    *,
    stage: str,
    blockers: list[str] | tuple[str, ...] = (),
) -> dict[str, list[str]]:
    """Separate affected and future documents from current stage blockers."""
    if not manifest or stage not in EXECUTION_STAGES:
        return {"affected": [], "deferred": []}
    stage_index = EXECUTION_STAGES.index(stage)
    blocker_paths = {str(item) for item in blockers if str(item).endswith(".md")}
    affected: list[str] = []
    deferred: list[str] = []
    for raw in manifest.get("artifacts", []):
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "")
        if not path or path in blocker_paths:
            continue
        status = str(raw.get("status") or "current")
        required_for = {
            str(item) for item in (raw.get("required_for") or []) if str(item) in EXECUTION_STAGES
        }
        if status in {"stale_soft", "evidence_stale"}:
            affected.append(path)
            continue
        if (
            raw.get("completeness") != "complete"
            and stage not in required_for
            and any(EXECUTION_STAGES.index(item) > stage_index for item in required_for)
        ):
            deferred.append(path)
    return {
        "affected": sorted(set(affected)),
        "deferred": sorted(set(deferred)),
    }


def _compute_execution_stage(
    *,
    project_id: str,
    plan_status: str,
    manifest: dict[str, Any] | None,
    project_root: Path | str | None = None,
    review_verdict: str | None = None,
    review_blockers_count: int = 0,
    workflow_stage: str = "requirements",
) -> dict[str, Any]:
    """Compute the authoritative workflow stage from disk-backed inputs."""
    if not project_id:
        return {"stage": "requirements", "reason": "no_project", "blockers": []}
    if workflow_stage == "documentation":
        return {
            "stage": "documentation",
            "reason": "documentation_in_progress",
            "blockers": list(CORE_PROJECT_ARTIFACTS),
        }
    if manifest is None:
        return {
            "stage": "requirements",
            "reason": "manifest_unavailable",
            "blockers": [".plan-agent/manifest.json"],
        }

    artifacts = {
        str(item.get("path")): item
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("path")
    }
    light = is_light_project_template(manifest)
    l2_stale = [
        path
        for path, item in artifacts.items()
        if item.get("status") == "stale"
    ]
    if l2_stale and not light:
        return {
            "stage": "requirements",
            "reason": "l2_stale",
            "blockers": sorted(l2_stale),
        }
    # A workflow that has already left requirements must not rewind to the
    # first-round scope card just because SCOPE.md ids are still empty.
    if not light and workflow_stage not in {"design", "implementation", "verification", "release"}:
        scope = artifacts.get("SCOPE.md") or {}
        scope_ids = set(scope.get("ids") or [])
        scope_blockers: list[str] = []
        if scope.get("status") != "current":
            scope_blockers.append("SCOPE.md")
        if not any(str(value).startswith("REQ-") for value in scope_ids):
            scope_blockers.append("REQ")
        if not any(str(value).startswith("AC-") for value in scope_ids):
            scope_blockers.append("AC")
        if scope_blockers:
            return {
                "stage": "requirements",
                "reason": "scope_incomplete",
                "blockers": scope_blockers,
            }
    if not light:
        design_blockers = [
            path
            for path in ("DESIGN.md", "TECH-DESIGN.md")
            if (artifacts.get(path) or {}).get("status") != "current"
        ]
        if design_blockers:
            return {
                "stage": "design",
                "reason": "design_incomplete",
                "blockers": design_blockers,
            }
    verification_names = ("VERIFY.md",) if light else ("VERIFY.md", "RELEASE.md")
    verification_blockers = [
        path
        for path in verification_names
        if (artifacts.get(path) or {}).get("status") != "current"
    ]
    if verification_blockers:
        return {
            "stage": "verification",
            "reason": "verification_artifact_stale",
            "blockers": verification_blockers,
        }
    verdict = str(review_verdict or "").strip().casefold()
    if verdict != "pass" or int(review_blockers_count or 0) > 0:
        # Ordinary mode: pending review is optional wrap-up, not a hard
        # execution wall. Runaway still enforces verification via its own
        # checkpoint / write gate, not this snapshot status.
        wrap_warnings = ["review"]
        if int(review_blockers_count or 0) > 0:
            wrap_warnings.append("review_blockers")
        return {
            "stage": "verification",
            "reason": "verification_pending",
            "blockers": [],
            "warnings": wrap_warnings,
        }
    return {"stage": "release", "reason": "verification_passed", "blockers": []}


def compute_execution_stage(
    *,
    project_id: str,
    plan_status: str,
    manifest: dict[str, Any] | None,
    project_root: Path | str | None = None,
    review_verdict: str | None = None,
    review_blockers_count: int = 0,
    workflow_stage: str = "requirements",
) -> dict[str, Any]:
    """Return the backend-authoritative stage snapshot consumed by every UI surface.

    ``_compute_execution_stage`` keeps the existing gate decisions. This wrapper
    adds the shared status vocabulary so a warning or in-progress document batch
    cannot be mistaken for an execution blocker.
    """
    result = _compute_execution_stage(
        project_id=project_id,
        plan_status=plan_status,
        manifest=manifest,
        project_root=project_root,
        review_verdict=review_verdict,
        review_blockers_count=review_blockers_count,
        workflow_stage=workflow_stage,
    )
    blockers = [str(item) for item in result.get("blockers", []) if str(item).strip()]
    warnings = [str(item) for item in result.get("warnings", []) if str(item).strip()]
    missing = [str(item) for item in result.get("missing", blockers) if str(item).strip()]

    if result.get("stage") == "documentation":
        artifacts = {
            str(item.get("path")): item
            for item in (manifest or {}).get("artifacts", [])
            if isinstance(item, dict) and item.get("path")
        }
        missing = [
            path
            for path in CORE_PROJECT_ARTIFACTS
            if (artifacts.get(path) or {}).get("status") != "current"
        ]
        blockers = []
        warnings.extend(f"文档整理中：{path}" for path in missing)

    content_lint = result.get("content_lint")
    if isinstance(content_lint, dict):
        warnings.extend(
            f"文档待完善：{item}"
            for item in (content_lint.get("missing") or [])
            if str(item).strip()
        )
    warnings = sorted(set(warnings))
    return {
        **result,
        "status": "blocked" if blockers else (
            "in_progress" if result.get("stage") == "documentation" else "ready"
        ),
        "blockers": sorted(set(blockers)),
        "missing": sorted(set(missing)),
        "warnings": warnings,
    }


@dataclass(frozen=True, slots=True)
class AcceptanceSpec:
    display: str
    script_rel: str = ""
    expected_exit_code: int = 0
    argv: tuple[str, ...] = ()

    @property
    def is_python(self) -> bool:
        return bool((self.script_rel or "").strip())


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_project_id(project_id: str) -> str:
    text = project_id.strip().lower().replace("_", "-")
    if not text or not _PROJECT_ID_RE.fullmatch(text):
        raise ProjectModeError(
            "project id must be lowercase letters, digits, hyphens; start with a letter"
        )
    if text in {_TEMPLATE_DIRNAME, "workspace"}:
        raise ProjectModeError(f"reserved project id: {text}")
    return text


def project_root_rel(project_id: str) -> str:
    pid = normalize_project_id(project_id)
    return f"workspace/{pid}"


def project_dir(paths: AgentPaths, project_id: str) -> Path:
    return paths.workspace / normalize_project_id(project_id)


def template_dir(paths: AgentPaths) -> Path:
    return paths.workspace / _TEMPLATE_DIRNAME


def is_project_dir(path: Path) -> bool:
    """A workspace folder is a project when it has PROJECT.md, a manifest, or a legacy TASKS.md."""
    return any((path / marker).is_file() for marker in _PROJECT_MARKERS)


def list_projects(paths: AgentPaths) -> list[str]:
    root = paths.workspace
    if not root.is_dir():
        return []
    found: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name == _TEMPLATE_DIRNAME:
            continue
        if is_project_dir(entry):
            found.append(entry.name)
    return found


def ensure_template(paths: AgentPaths) -> Path:
    target = template_dir(paths)
    target.mkdir(parents=True, exist_ok=True)
    for name in _TEMPLATE_FILES:
        dest = target / name
        existing = ""
        if dest.is_file():
            try:
                existing = dest.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        if (
            not existing.strip()
            or existing.strip() == f"# template {name}"
            or existing.strip() == _template_body(name, "template").strip()
        ):
            # Keep {{project_id}} so create_project can substitute the real id.
            dest.write_text(_template_body(name, "{{project_id}}"), encoding="utf-8")
    return target


def _template_body(name: str, project_id: str) -> str:
    bodies = {
        "PROJECT.md": (
            "# {{project_id}} · 项目章程\n\n"
            "## 目标\n\n- 待填写项目目标与用户价值。\n\n"
            "## 用户与场景\n\n- 待填写主要用户和使用场景。\n\n"
            "## 范围摘要\n\n- 第一轮范围见 `SCOPE.md`。\n\n"
            "## 非目标\n\n- 待填写本阶段明确不做的内容。\n"
        ),
        "SCOPE.md": (
            "# {{project_id}} · 范围与验收\n\n"
            "## REQ-001\n\n- 待填写用户故事、范围和边界。\n\n"
            "## AC-001\n\n- 待填写可验证的验收标准。\n\n"
            "## 边界\n\n- 待填写约束、非目标和风险。\n"
        ),
        "DESIGN.md": (
            "# {{project_id}} · UX 设计\n\n"
            "## UX-001\n\n- 待填写页面流程、交互状态和异常路径。\n"
        ),
        "TECH-DESIGN.md": (
            "# {{project_id}} · 技术设计\n\n"
            "## TD-001\n\n- 待填写架构、数据模型、API、依赖和技术风险。\n"
        ),
        "VERIFY.md": (
            "# {{project_id}} · 验证矩阵\n\n"
            "| V-001 | AC-001 | 待填写测试/Gate | 待填写 L1 证据 |\n"
            "|---|---|---|---|\n"
        ),
        "RELEASE.md": (
            "# {{project_id}} · 发布\n\n"
            "## REL-001\n\n- 待填写发布、迁移、回滚和人工验收清单。\n"
        ),
        "MAP.md": (
            "# {{project_id}} · 代码地图\n\n"
            "## 入口\n\n- 待补充源码入口和模块指针。\n"
        ),
    }
    return bodies.get(name, f"# {project_id} · {name}\n").replace("{{project_id}}", project_id)


def _light_template_body(name: str, project_id: str) -> str:
    bodies = {
        "PROJECT.md": (
            "# {{project_id}}\n\n"
            "直接写代码即可。需要拆任务或补文档时再说。\n"
        ),
        "VERIFY.md": (
            "# {{project_id}} · 验证\n\n"
            "（暂无验证项）\n"
        ),
    }
    return bodies.get(name, f"# {project_id} · {name}\n").replace("{{project_id}}", project_id)


def _copy_template_file(src: Path, dest: Path, project_id: str) -> None:
    body = src.read_text(encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    filled = (
        body.replace("{{project_id}}", project_id)
        .replace("# template ·", f"# {project_id} ·")
        .replace("# template ", f"# {project_id} ")
    )
    dest.write_text(filled, encoding="utf-8")


def create_project(
    paths: AgentPaths,
    project_id: str,
    *,
    template: str | None = None,
    project_template: str | None = None,
) -> Path:
    pid = normalize_project_id(project_id)
    dest = project_dir(paths, pid)
    if dest.exists() and any(dest.iterdir()):
        raise ProjectModeError(f"project already exists: workspace/{pid}")
    recipe_id = (template or "").strip()
    selected_template = normalize_project_template(project_template)
    if recipe_id in {"light", "standard", "lite", "full"}:
        selected_template = normalize_project_template(recipe_id)
        recipe_id = ""
    dest.mkdir(parents=True, exist_ok=True)
    if selected_template == "light":
        for name in LIGHT_SCAFFOLD_ARTIFACTS:
            (dest / name).write_text(_light_template_body(name, pid), encoding="utf-8")
    else:
        src = ensure_template(paths)
        for name in _TEMPLATE_FILES:
            src_file = src / name
            if src_file.is_file():
                _copy_template_file(src_file, dest / name, pid)
            elif name in _TEMPLATE_FILES:
                (dest / name).write_text(_template_body(name, pid), encoding="utf-8")
    try:
        from project_env import ensure_project_env

        ensure_project_env(paths, pid)
    except Exception:
        pass
    if recipe_id:
        from scaffold_recipes import run_scaffold_after_create

        result = run_scaffold_after_create(paths, pid, recipe_id)
        if not result.get("ok"):
            failed = result.get("failed_step") or "unknown"
            err = result.get("error") or f"scaffold step {failed} failed"
            raise ProjectModeError(f"scaffold {recipe_id!r} failed: {err}")
    from project_manifest import bootstrap_manifest

    bootstrap_manifest(dest, pid, project_template=selected_template)
    return dest


def read_project_template(paths: AgentPaths, project_id: str) -> str:
    """Return the durable project artifact template (``light`` | ``standard``)."""
    from project_manifest import load_manifest, manifest_path

    try:
        pid = normalize_project_id(project_id)
    except ProjectModeError:
        return DEFAULT_PROJECT_TEMPLATE
    try:
        manifest = load_manifest(manifest_path(project_dir(paths, pid)))
    except Exception:
        return DEFAULT_PROJECT_TEMPLATE
    return project_template_of(manifest)


def upgrade_project_to_standard(paths: AgentPaths, project_id: str) -> dict[str, Any]:
    """Backfill missing seven-file artifacts and re-enable standard L2 gates."""
    from project_manifest import build_manifest, manifest_path, save_manifest

    pid = normalize_project_id(project_id)
    root = project_dir(paths, pid)
    if not root.is_dir():
        raise ProjectModeError(f"project not found: workspace/{pid}")
    previous = read_project_template(paths, pid)
    created: list[str] = []
    src = ensure_template(paths)
    for name in _TEMPLATE_FILES:
        dest = root / name
        existing = ""
        if dest.is_file():
            try:
                existing = dest.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        if existing.strip():
            continue
        src_file = src / name
        if src_file.is_file():
            _copy_template_file(src_file, dest, pid)
        elif name in _TEMPLATE_FILES:
            dest.write_text(_template_body(name, pid), encoding="utf-8")
        else:
            continue
        created.append(name)
    try:
        from project_env import ensure_project_env

        ensure_project_env(paths, pid)
    except Exception:
        pass
    manifest = build_manifest(root, pid, project_template="standard")
    save_manifest(manifest_path(root), manifest)
    return {
        "project_id": pid,
        "previous_template": previous,
        "template": "standard",
        "created": created,
        "already_standard": previous == "standard" and not created,
    }


def _is_template_document(name: str, project_id: str, text: str) -> bool:
    normalized = text.strip()
    return (
        not normalized
        or normalized == f"# template {name}"
        or normalized == _template_body(name, project_id).strip()
        or normalized == _template_body(name, "template").strip()
        or normalized == _light_template_body(name, project_id).strip()
        or normalized == _light_template_body(name, "template").strip()
    )


def organize_project_documents(paths: AgentPaths, project_id: str) -> dict[str, Any]:
    pid = normalize_project_id(project_id)
    root = project_dir(paths, pid)
    if not root.is_dir():
        raise ProjectModeError(f"project not found: workspace/{pid}")

    documents = {
        "PROJECT.md": (
            f"# {pid} · 项目章程\n\n"
            "## 目标\n\n"
            "说明要解决的问题、目标用户和可衡量结果。\n\n"
            "## 范围\n\n"
            "- REQ-001：本轮要交付的用户价值。\n"
            "- AC-001：用户可以通过明确步骤验证结果。\n\n"
            "## 非目标\n\n"
            "- 本轮不包含未在范围中确认的功能、重构或基础设施。\n\n"
            "## 待确认问题\n\n"
            "- 用户、数据、合规和发布约束待确认。\n"
        ),
        "DESIGN.md": (
            f"# {pid} · 需求与设计\n\n"
            "## 用户流程\n\n"
            "- UX-001：描述主流程、关键状态、异常路径和空状态。\n\n"
            "## 技术方案\n\n"
            "- TD-001：描述模块边界、数据流、接口、依赖和失败处理。\n\n"
            "## 关键决策\n\n"
            "- ADR-001：记录方案选择、替代方案和取舍。\n\n"
            "## 设计验收\n\n"
            "- AC-001：设计能够支撑一个小任务独立实现和验证。\n"
        ),
        "VERIFY.md": (
            f"# {pid} · 验证计划与证据\n\n"
            "## V-001 · 第一条用户流程\n\n"
            "- 对应验收：AC-001\n"
            "- 验证方法：运行与 AC-001 对口的测试或人工验收步骤（以退出码为准）。\n"
            "- 预期结果：主流程、异常路径和数据结果符合 DESIGN.md。\n"
            "- 实际证据：待执行。\n\n"
            "## 发布检查\n\n"
            "- 测试结果、迁移/回滚影响和人工发布确认：待补充。\n"
        ),
    }
    generated: list[str] = []
    preserved: list[str] = []
    for name, content in documents.items():
        path = root / name
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if _is_template_document(name, pid, existing):
            path.write_text(content, encoding="utf-8")
            generated.append(name)
        else:
            preserved.append(name)
    return {
        "project_id": pid,
        "generated": generated,
        "preserved": preserved,
        "artifacts": list(CORE_PROJECT_ARTIFACTS),
    }


def documentation_ready_for_design(paths: AgentPaths, project_id: str) -> tuple[bool, list[str]]:
    artifacts = read_project_artifacts(paths, project_id)
    checks = {
        "PROJECT.md": all(token in artifacts.get("PROJECT.md", "") for token in ("REQ-", "AC-")),
        "DESIGN.md": all(token in artifacts.get("DESIGN.md", "") for token in ("UX-", "TD-")),
        "VERIFY.md": all(token in artifacts.get("VERIFY.md", "") for token in ("V-", "AC-")),
    }
    return all(checks.values()), [name for name, ok in checks.items() if not ok]


def _read_project_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return ""


def _first_stable_id(text: str, prefix: str, default: str) -> str:
    match = re.search(rf"\b{re.escape(prefix)}-\d{{3,}}\b", text, re.IGNORECASE)
    return match.group(0).upper() if match else default


def _migration_excerpt(text: str, limit: int = 8000) -> str:
    excerpt = text.strip()
    if len(excerpt) > limit:
        return excerpt[:limit] + "\n…（迁移摘录已截断）"
    return excerpt or "（旧项目未提供此内容，待补充）"


def _write_migration_file(path: Path, body: str) -> bool:
    if path.is_file() and _read_project_text(path).strip():
        return False
    path.write_text(body.rstrip() + "\n", encoding="utf-8")
    return True


def migrate_legacy_project(paths: AgentPaths, project_id: str) -> bool:
    """Migrate a pre-58b project to the seven-artifact baseline once."""
    pid = normalize_project_id(project_id)
    root = project_dir(paths, pid)
    if not root.is_dir():
        raise ProjectModeError(f"project not found: workspace/{pid}")
    from project_manifest import manifest_path

    if manifest_path(root).is_file():
        return False

    project_text = _read_project_text(root / "PROJECT.md")
    map_text = _read_project_text(root / "MAP.md")
    env_text = _read_project_text(root / "ENV.md")
    req_id = _first_stable_id(project_text, "REQ", "REQ-001")
    ac_id = _first_stable_id(project_text, "AC", "AC-001")
    changed = False
    changed |= _write_migration_file(
        root / "PROJECT.md",
        _template_body("PROJECT.md", pid) + "\n## 迁移备注\n\n- 该项目由旧计划域迁移，原内容保持不变或待补充。\n",
    )
    changed |= _write_migration_file(
        root / "SCOPE.md",
        (
            f"# {pid} · 范围与验收\n\n"
            f"## {req_id}\n\n- 从旧 `PROJECT.md` 迁移的需求范围，待用户确认。\n\n"
            f"## {ac_id}\n\n- 从旧项目验收内容迁移；缺失部分待补充为可验证条件。\n\n"
            "## 边界\n\n- 旧项目边界从原计划域迁移，新增范围须重新规划。\n\n"
            "## 旧 PROJECT.md 摘录\n\n"
            f"{_migration_excerpt(project_text)}\n"
        ),
    )
    changed |= _write_migration_file(
        root / "DESIGN.md",
        (
            f"# {pid} · UX 设计\n\n## UX-001\n\n"
            "- 从旧项目代码地图推断的设计入口；细节待确认。\n\n"
            "## 旧 MAP.md 摘录\n\n"
            f"{_migration_excerpt(map_text)}\n"
        ),
    )
    changed |= _write_migration_file(
        root / "TECH-DESIGN.md",
        (
            f"# {pid} · 技术设计\n\n## TD-001\n\n"
            "- 从旧项目代码地图保留技术入口；架构、API 和风险待确认。\n\n"
            "## 旧 MAP.md 摘录\n\n"
            f"{_migration_excerpt(map_text)}\n"
        ),
    )
    # TASKS.md is neither created nor rewritten (task subsystem removed).
    changed |= _write_migration_file(
        root / "VERIFY.md",
        (
            f"# {pid} · 验证矩阵\n\n"
            "| V-001 | "
            f"{ac_id} | 旧项目验收与 ENV 质量命令 | 待重跑 L1 证据 |\n"
            "|---|---|---|---|\n\n"
            "## ENV.md 摘录\n\n"
            f"{_migration_excerpt(env_text)}\n"
        ),
    )
    changed |= _write_migration_file(
        root / "RELEASE.md",
        (
            f"# {pid} · 发布\n\n## REL-001\n\n"
            "- 迁移基线：发布前重跑 VERIFY 中的 L1 命令。\n"
            "- 迁移/回滚：待根据项目技术设计补充。\n"
            "- 人工验收：待用户确认。\n"
        ),
    )
    changed |= _write_migration_file(
        root / "MAP.md",
        f"# {pid} · 代码地图\n\n## 入口\n\n{_migration_excerpt(map_text)}\n",
    )
    if not (root / "ENV.md").is_file():
        try:
            from project_env import ensure_project_env

            ensure_project_env(paths, pid)
            changed = True
        except Exception:
            pass
    from project_manifest import bootstrap_manifest

    if not manifest_path(root).is_file():
        bootstrap_manifest(root, pid, tier="normal", content_origin="migrated", change_scope="normal")
    return changed




_ISO_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")



def utc_today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")



# Stable order for prompts and goals. Light scaffolds omit the standard-only docs.
_CONTEXT_PLAN_FILES = (  # TASKS.md intentionally excluded: task subsystem removed
    "PROJECT.md",
    "VERIFY.md",
    "ENV.md",
    "MAP.md",
    "SCOPE.md",
    "DESIGN.md",
    "TECH-DESIGN.md",
    "RELEASE.md",
)


def existing_plan_files(paths: AgentPaths, project_id: str) -> list[str]:
    """Plan documents that exist for this project, in a stable order."""
    try:
        root = project_dir(paths, project_id)
    except ProjectModeError:
        return []
    return [name for name in _CONTEXT_PLAN_FILES if (root / name).is_file()]


def plan_files_fingerprint(paths: AgentPaths, project_id: str) -> str:
    """Content hash of plan documents, including files that are absent."""
    try:
        root = project_dir(paths, project_id)
    except ProjectModeError:
        return ""
    digest = hashlib.sha256()
    for name in _CONTEXT_PLAN_FILES:
        digest.update(name.encode("utf-8"))
        path = root / name
        if path.is_file():
            digest.update(b"\x01")
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"\x00")
        else:
            digest.update(b"\x00")
    return digest.hexdigest()


def build_project_goal(
    *,
    project_root: str,
    plan_status: str,
    plan_files: list[str] | None = None,
) -> str:
    lines = [
        f"项目根：{project_root}",
    ]
    names = list(plan_files) if plan_files is not None else ["MAP.md"]
    if "MAP.md" in names:
        lines.append(f"地图：{project_root}/MAP.md")
    if plan_files is not None:
        shown = "、".join(names) if names else "（无）"
        lines.append(f"计划文件：{shown}")
    lines.append(f"计划状态：{plan_status or 'draft'}")
    return "\n".join(lines)


def normalize_meta_path(path: str) -> str:
    return path.strip().replace("\\", "/").strip("/")


def _path_text(value: object) -> str:
    return value.strip().replace("\\", "/").lstrip("/") if isinstance(value, str) else ""


def project_id_from_root(project_root: str) -> str:
    root = normalize_meta_path(project_root)
    if root.startswith("workspace/"):
        return root[len("workspace/") :].split("/", 1)[0]
    return root.split("/", 1)[0] if root else ""


def project_path_rel(path: str, project_root: str) -> str | None:
    """Return path relative to project root, or None if outside.

    Accepts both ``workspace/<id>/…`` (meta style) and ``<id>/…``
    (write_text workspace-relative style).
    """
    normalized = _path_text(path)
    root = normalize_meta_path(project_root)
    if not normalized or not root:
        return None
    if normalized == root:
        return ""
    if normalized.startswith(f"{root}/"):
        return normalized[len(root) + 1 :]
    pid = project_id_from_root(root)
    if pid:
        if normalized == pid:
            return ""
        if normalized.startswith(f"{pid}/"):
            return normalized[len(pid) + 1 :]
    return None


def is_active_project(meta: object) -> bool:
    root = getattr(meta, "project_root", "") or ""
    shell = getattr(meta, "active_shell", "") or ""
    return bool(root.strip()) and shell == "project"


def project_plan_gate_open(meta: object) -> bool:
    """Plan confirmation gate removed (no task subsystem): never open."""
    del meta
    return False


def plan_allows_code_writes(plan_status: str) -> bool:
    return plan_status == "confirmed"


_DIRECT_IMPLEMENT_EXTRA_MARKERS = (
    "直接实现",
    "直接写代码",
    "直接推进",
    "不要只出计划",
    "不要只给计划",
    "不要再问我是否开始",
    "不要问我是否开始",
    "现在就实际",
    "实际改文件",
    "写出可运行代码",
    "just implement",
    "implement now",
)
VALID_PROJECT_ENTRIES = frozenset({"", "plan", "direct"})


def normalize_project_entry(value: object) -> str:
    """Persistable ordinary-mode entry: ``""`` | ``plan`` | ``direct``."""
    text = str(value or "").strip()
    return text if text in VALID_PROJECT_ENTRIES else ""


def is_direct_implement_request(user_text: str) -> bool:
    """True when the user explicitly wants code now, not another plan round."""
    text = (user_text or "").strip()
    if not text:
        return False
    try:
        from terminal_plan import is_skip_plan_turn

        if is_skip_plan_turn(text):
            return True
    except Exception:
        pass
    lower = text.casefold()
    return any(marker.casefold() in lower for marker in _DIRECT_IMPLEMENT_EXTRA_MARKERS)


def ordinary_project_session(session: object) -> bool:
    """True when this is an ordinary (non-runaway) bound project session."""
    meta = getattr(session, "meta", session)
    if meta is None:
        return False
    if (getattr(meta, "active_shell", "") or "") != "project":
        return False
    if not (getattr(meta, "project_id", "") or "").strip():
        return False
    if not (getattr(meta, "project_root", "") or "").strip():
        return False
    return True


def ordinary_skip_stage_walls(
    session: object,
    *,
    agent_paths: AgentPaths | None = None,
) -> bool:
    """Light template or ``project_entry=direct``: textbook stages must not hard-stop writes.

    「规划后开工」(``project_entry=plan``) on a standard project keeps stage walls.
    Light projects skip walls even if the user later confirmed the plan.
    """
    if not ordinary_project_session(session):
        return False
    meta = getattr(session, "meta", session)
    entry = normalize_project_entry(getattr(meta, "project_entry", ""))
    if entry == "direct":
        return True
    paths = agent_paths or getattr(session, "paths", None)
    pid = str(getattr(meta, "project_id", "") or "").strip()
    if paths is not None and pid:
        try:
            if read_project_template(paths, pid) == "light":
                return True
        except Exception:
            pass
    return False


def _ordinary_question_or_recall(user_text: str, turn_intent: str = "") -> bool:
    """True when this message is a question or recall, not an implement request."""
    if is_direct_implement_request(user_text):
        return False
    labeled = (turn_intent or "").strip()
    if labeled in {"qa", "recall"}:
        return True
    text = (user_text or "").strip()
    if not text:
        return False
    from turn_intent import classify_turn

    return classify_turn(text) in {"qa", "recall"}


def should_treat_ordinary_direct_implement(
    session: object,
    user_text: str = "",
    *,
    turn_intent: str = "",
) -> bool:
    """Ordinary-mode turn gate: ``project_entry=direct``, phrase, or execute intent.

    ``project_entry=plan`` keeps the plan confirm gate unless this turn is an
    explicit「直接实现」switch. Stages no longer veto the direct path.
    A question or recall that is not an explicit 直接实现 request does not count.
    Empty text stays sticky so the tool filter can hide plan_partner.
    """
    if not ordinary_project_session(session):
        return False
    meta = getattr(session, "meta", None)
    explicit = is_direct_implement_request(user_text)
    if _ordinary_question_or_recall(user_text, turn_intent):
        return False
    if (turn_intent or "").strip() == "requirements" and normalize_project_entry(
        getattr(meta, "project_entry", "")
    ) == "direct":
        # Already building directly: a long message is follow-up work, not a
        # brief to summarize, so keep the tools.
        return True
    # A keyword hit inside a question must not confirm the plan or flip
    # project_entry to direct. Real requests still carry an imperative frame.
    from turn_intent import _has_imperative_frame, _is_inquiry

    if (
        (user_text or "").strip()
        and _is_inquiry(user_text)
        and not explicit
        and not _has_imperative_frame(user_text)
    ):
        return False
    entry = normalize_project_entry(getattr(meta, "project_entry", ""))
    execute = (turn_intent or "").strip() == "execute"
    if entry == "plan" and not explicit:
        return False
    if entry == "direct" or explicit or execute:
        return True
    return False


def should_skip_ordinary_plan_spawn(
    session: object,
    user_text: str = "",
    *,
    turn_intent: str = "",
) -> bool:
    """Do not pre-spawn plan_partner when the user is implementing, not planning."""
    if not ordinary_project_session(session):
        return False
    intent = (turn_intent or "").strip()
    # Questions and reply-only lines are answered directly. Do not pre-spawn
    # plan_partner, even when project_entry is still direct.
    if _ordinary_question_or_recall(user_text, intent):
        return True
    if intent in {"plan", "requirements"}:
        return False
    if should_treat_ordinary_direct_implement(session, user_text, turn_intent=intent):
        return True
    return intent == "execute"


def maybe_auto_confirm_plan_for_direct_implement(session: object) -> str | None:
    """Arm one ordinary execute request without mutating persisted stage facts."""
    if not ordinary_project_session(session):
        return None
    setattr(session, "next_work_intent", "execute")
    setattr(session, "next_task_scope", "")
    return "[项目] 检测到直接实现意图；本回合已切换为 execute，不修改项目阶段或计划确认状态。"


def is_under_project_root(path: str, project_root: str) -> bool:
    return project_path_rel(path, project_root) is not None


def is_project_artifact_path(path: str, project_root: str) -> bool:
    rel = project_path_rel(path, project_root)
    if rel is None or not rel or "/" in rel:
        return False
    return rel in PROJECT_ARTIFACTS



def is_plan_domain_path(path: str, project_root: str) -> bool:
    """True when path targets a plan-domain basename under project root (Phase 39 B5)."""
    rel = project_path_rel(path, project_root)
    if rel is None or not rel or "/" in rel:
        return False
    return rel in PLAN_DOMAIN_FILES




def normalize_delivery_profile(value: Any) -> ProjectDeliveryProfile:
    """Map CLI/meta values to solo|ritual (Phase 47 · DELIVERABLE-REVIEW §6)."""
    raw = str(value or "").strip().casefold()
    if raw in {"ritual", "strict", "严格"}:
        return "ritual"
    if raw in {"solo", "宽松", "relaxed"}:
        return "solo"
    if raw in VALID_PROJECT_DELIVERY_PROFILES:
        return raw  # type: ignore[return-value]
    return DEFAULT_PROJECT_DELIVERY_PROFILE


def get_delivery_profile(meta: Any) -> ProjectDeliveryProfile:
    return normalize_delivery_profile(getattr(meta, "project_delivery_profile", "solo"))



def set_delivery_profile(session: Any, mode: str) -> ProjectDeliveryProfile:
    """Persist delivery profile on session meta."""
    profile = normalize_delivery_profile(mode)
    session.meta.project_delivery_profile = profile
    from session import utc_now_iso

    session.meta.updated_at = utc_now_iso()
    return profile


def extract_run_evolved_paths(tool_name: str, arguments: dict[str, object]) -> list[str]:
    if tool_name != "run_evolved":
        return []
    paths: list[str] = []
    outer_path = arguments.get("path")
    if isinstance(outer_path, str) and outer_path.strip():
        paths.append(outer_path)
    inner = arguments.get("arguments")
    if isinstance(inner, dict):
        inner_path = inner.get("path")
        if isinstance(inner_path, str) and inner_path.strip():
            paths.append(inner_path)
        inner_dest = inner.get("dest")
        if isinstance(inner_dest, str) and inner_dest.strip():
            paths.append(inner_dest)
    return paths


def _ordinary_action_block_reason(
    *,
    project_root: str,
    tool_name: str,
    arguments: dict[str, object],
    ordinary_work_intent: str,
) -> str | None:
    """Apply request-local ordinary policy before legacy stage compatibility."""
    intent = ordinary_work_intent if ordinary_work_intent in {"read_only", "plan_only", "execute"} else "execute"
    if tool_name != "run_evolved":
        return None
    evolved = arguments.get("tool_name")
    evolved_name = evolved.strip() if isinstance(evolved, str) else ""
    is_write = evolved_name in _WRITE_TOOLS or evolved_name == "patch_file"
    is_command = evolved_name in _CODING_TOOLS
    if intent == "read_only" and (is_write or is_command):
        return "当前请求是只读操作，不能写入文件或执行项目命令"
    if intent == "plan_only" and is_command:
        return "当前请求仅规划，不能修改业务源码或执行实现命令"

    if is_write or is_command:
        root = project_root.strip()
        for path in extract_run_evolved_paths(tool_name, arguments):
            if path and root and not is_under_project_root(path, root):
                return f"项目写入仅限当前项目目录：{root}"

    if intent == "plan_only" and is_write:
        paths = [path for path in extract_run_evolved_paths(tool_name, arguments) if path]
        if any(not is_plan_domain_path(path, project_root) for path in paths):
            return "当前请求仅规划，不能修改业务源码"
    return None


def project_mode_block_reason(
    *,
    active_shell: str,
    project_root: str,
    plan_status: str = "",
    tool_name: str,
    arguments: dict[str, object],
    agent_paths: AgentPaths | None = None,
    workflow_stage: str = "",
    runaway_enabled: bool = False,
    bug_fix_lane: bool = False,
    runaway_v2_write_scope: tuple[str, ...] | None = None,
    skip_stage_walls: bool = False,
    ordinary_work_intent: str = "execute",
    ordinary_task_scope: str | None = None,
) -> str | None:
    """Return user-facing block reason, or None if allowed.

    Only safety guards remain (request intent, project-root scope, no-project
    side effects, write_evolve / evolve clone in project windows). Plan/stage/
    task gates were removed: planning is optional guidance via skills.
    Legacy keyword arguments are accepted and ignored for compatibility.
    """
    del plan_status, agent_paths, workflow_stage, runaway_enabled, bug_fix_lane
    del runaway_v2_write_scope, skip_stage_walls, ordinary_task_scope
    root = project_root.strip()
    evolved = arguments.get("tool_name") if tool_name == "run_evolved" else None
    evolved_name = evolved.strip() if isinstance(evolved, str) else ""
    if active_shell == "project":
        ordinary_block = _ordinary_action_block_reason(
            project_root=project_root,
            tool_name=tool_name,
            arguments=arguments,
            ordinary_work_intent=ordinary_work_intent,
        )
        if ordinary_block:
            return ordinary_block

    if active_shell == "project" and tool_name == "run_evolved" and evolved_name == "write_evolve":
        return (
            "项目窗口禁止 write_evolve（养 agent 与做产物分离）。"
            "请点顶栏「+ 对话」开普通对话后再造工具，"
            "或由助手 propose_context_switch(action=session.new, target=current)。"
        )

    if active_shell == "project" and tool_name == "run_evolved" and evolved_name == "git_clone":
        inner = arguments.get("arguments")
        if isinstance(inner, dict) and inner.get("target") == "evolve_tools":
            return "project 模式禁止向 evolve/tools clone；请切换到 grow 壳沉淀能力"

    if active_shell == "project" and not root:
        if tool_name == "run_evolved" and (
            evolved_name in _CODING_TOOLS
            or evolved_name in _WRITE_TOOLS
            or evolved_name in {"git_clone", "write_evolve"}
        ):
            return (
                "当前项目窗口未绑定项目；已停止副作用操作。"
                "请先打开或新建项目，再执行写入、运行、测试或创建工具。"
            )

    if not root:
        return None

    if tool_name == "run_evolved" and evolved_name == "patch_file":
        for path in extract_run_evolved_paths(tool_name, arguments):
            if path and not is_under_project_root(path, project_root):
                return f"patch_file 仅限项目目录内：{project_root}"
    return None


def format_project_overlay(
    *,
    project_root: str,
    project_id: str,
    ordinary_work_intent: str = "execute",
    plan_files: list[str] | None = None,
    active_doc: str = "",
    active_doc_selection: str = "",
    **_legacy: Any,
) -> str:
    """Project context overlay. No plan/stage/task gates: TASKS.md is not read.

    Legacy keyword arguments (plan_status, task_stats, runaway_*, ...) are
    accepted and ignored so older callers keep working.
    """
    lines = [
        "[项目模式 · project]",
        f"project_root: {project_root}",
        f"project_id: {project_id}",
    ]
    if ordinary_work_intent == "read_only":
        lines.append("request_policy: 本回合只读；允许读取和状态查看，禁止写文件、运行命令和其它副作用")
    elif ordinary_work_intent == "plan_only":
        lines.append("request_policy: 本回合仅规划；允许读取和更新规划文档，禁止修改业务源码或执行实现命令")
    else:
        lines.append(
            "request_policy: 按用户明确请求执行；项目根目录、风险审批和真实验证是硬边界；"
            "没有计划确认、阶段或任务门槛。需要时可参考 plan-and-verify 等 Skill，但它们只是建议。"
        )
    if plan_files is not None:
        shown = ", ".join(plan_files) if plan_files else "(none)"
        lines.append(f"project_docs: {shown}")
        lines.append(
            "project_docs_hint: 这些是项目里已有的说明文档，按需 read_file；"
            "不要为了补齐模板去创建文档。完成以真实构建/测试结果为准。"
        )
    active = (active_doc or "").strip()
    if active:
        lines.append(f"active_doc: {active}")
        lines.append(
            "active_doc_hint: 用户正在文档阅读器中打开上面这篇。"
            "「这篇 / 这个文档 / 当前文档」即指它；改短、改标题、补一段时优先 patch_file 该路径。"
        )
        selection = (active_doc_selection or "").strip()
        if selection:
            clipped = selection if len(selection) <= 1200 else selection[:1199] + "…"
            lines.append("active_doc_selection: |")
            for line in clipped.splitlines() or [clipped]:
                lines.append(f"  {line}")
            lines.append(
                "active_doc_selection_hint: 用户选中了上述片段；「这段 / 选中内容」即指它。"
            )
    if sys.platform == "win32":
        lines.append(
            "tool_hint_win: Python 测试优先 run_command `python -m pytest …`，"
            "不要假定 pytest 已在 PATH。"
        )
    return "\n".join(lines)


def load_project_prompt(
    evolve_dir: Path,
    *,
    profile: str = DEFAULT_PROJECT_DELIVERY_PROFILE,
) -> str:
    """Assemble project-boundaries + delivery profile prompt (Phase 47 T-4706)."""
    prompts_dir = evolve_dir / "prompts"
    parts: list[str] = []
    entry = prompts_dir / "project.md"
    if entry.is_file():
        parts.append(entry.read_text(encoding="utf-8").strip())
    boundaries = prompts_dir / "project-boundaries.md"
    if boundaries.is_file():
        parts.append(boundaries.read_text(encoding="utf-8").strip())
    del profile  # ritual delivery profile removed; solo prompt is the only one
    delivery_name = "project-delivery-solo.md"
    delivery = prompts_dir / delivery_name
    if delivery.is_file():
        parts.append(delivery.read_text(encoding="utf-8").strip())
    elif not parts:
        legacy = prompts_dir / "project.md"
        if legacy.is_file():
            return legacy.read_text(encoding="utf-8").strip()
        return "[project prompts missing at evolve/prompts/]"
    return "\n\n".join(parts)



def project_doc_fingerprint(path: Path) -> str:
    """Fingerprint structural plan fields only (not every PROJECT.md edit)."""
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    headers = [line.strip() for line in text.splitlines() if line.strip().startswith("## ")]
    acceptance = _acceptance_section(text).strip()
    return json.dumps({"headers": headers, "acceptance": acceptance}, ensure_ascii=False)


def read_project_artifacts(paths: AgentPaths, project_id: str) -> dict[str, str]:
    root = project_dir(paths, project_id)
    out: dict[str, str] = {}
    for name in PROJECT_ARTIFACTS:
        file_path = root / name
        if file_path.is_file():
            try:
                out[name] = file_path.read_text(encoding="utf-8")
            except OSError:
                out[name] = ""
    return out


def _acceptance_section(project_md: str) -> str:
    marker = "## 验收标准"
    idx = project_md.find(marker)
    if idx < 0:
        return project_md
    return project_md[idx:]


def acceptance_command_to_argv(display: str) -> list[str]:
    """Split PROJECT acceptance display into argv (python, powershell, etc.)."""
    text = (display or "").strip()
    if not text:
        return []
    if sys.platform == "win32":
        return shlex.split(text, posix=False)
    return shlex.split(text, posix=True)


def parse_acceptance_command_display(project_md: str) -> str | None:
    """First executable ``命令：`…` `` under ## 验收标准 (any shell, not only python)."""
    section = _acceptance_section(project_md)
    for line in section.splitlines():
        match = _ACCEPT_CMD_RE.search(line)
        if match:
            return match.group(1).strip()
    return None


def parse_acceptance_spec(project_md: str) -> AcceptanceSpec | None:
    """Parse first executable ``命令：`…` `` under ## 验收标准 (python or shell)."""
    section = _acceptance_section(project_md)
    for line in section.splitlines():
        match = _ACCEPT_CMD_RE.search(line)
        if not match:
            continue
        command = match.group(1).strip()
        exit_match = _ACCEPT_EXIT_RE.search(line)
        expected = int(exit_match.group(1)) if exit_match else 0
        script_match = _PYTHON_SCRIPT_RE.search(command)
        if script_match:
            script = script_match.group(1).strip().replace("\\", "/").lstrip("/")
            if script.startswith("workspace/"):
                script = script.removeprefix("workspace/")
            return AcceptanceSpec(
                display=command,
                script_rel=script,
                expected_exit_code=expected,
            )
        argv = tuple(acceptance_command_to_argv(command))
        if not argv:
            continue
        return AcceptanceSpec(
            display=command,
            expected_exit_code=expected,
            argv=argv,
        )
    return None


def acceptance_workspace_path(project_id: str, spec: AcceptanceSpec) -> str:
    script = spec.script_rel.replace("\\", "/").lstrip("/")
    pid = normalize_project_id(project_id)
    if script.startswith(f"{pid}/"):
        return script
    return f"{pid}/{script}"


def acceptance_script_exists(paths: AgentPaths, project_id: str, spec: AcceptanceSpec) -> bool:
    if spec.is_python:
        rel = acceptance_workspace_path(project_id, spec)
        return (paths.workspace / rel).is_file()
    root = project_dir(paths, project_id)
    argv = list(spec.argv) or acceptance_command_to_argv(spec.display)
    for index, arg in enumerate(argv):
        lowered = arg.lower()
        if lowered in {"-file", "/file"} and index + 1 < len(argv):
            candidate = root / argv[index + 1].replace("\\", "/").lstrip("/")
            return candidate.is_file()
        if lowered.endswith((".py", ".ps1", ".bat", ".cmd", ".sh")):
            rel = arg.replace("\\", "/").lstrip("/")
            if (root / rel).is_file():
                return True
            workspace_rel = f"{normalize_project_id(project_id)}/{rel}"
            if (paths.workspace / workspace_rel).is_file():
                return True
    return bool(argv)


def run_acceptance_check(
    paths: AgentPaths,
    project_id: str,
    spec: AcceptanceSpec,
) -> dict[str, Any]:
    """Run PROJECT.md acceptance via run_command (python script or shell command)."""
    import sys

    from tools.builtin.run_evolved import run
    from tools.registry import ToolRegistry

    pid = normalize_project_id(project_id)
    registry = ToolRegistry.load(paths)
    if spec.is_python:
        rel = acceptance_workspace_path(project_id, spec)
        script_path = paths.workspace / rel
        if not script_path.is_file():
            return {
                "ok": False,
                "passed": False,
                "error": f"验收脚本不存在：workspace/{rel}",
                "command": spec.display,
                "path": f"workspace/{rel}",
                "expected_exit_code": spec.expected_exit_code,
            }
        script_abs = str(script_path.resolve())
        py = sys.executable
        if sys.platform == "win32":
            command = f'& "{py}" "{script_abs}"'
        else:
            command = f'"{py}" "{script_abs}"'
        path = f"workspace/{rel}"
    else:
        command = spec.display
        path = f"workspace/{pid}"

    tool_result = run(
        {
            "tool_name": "run_command",
            "arguments": {
                "command": command,
                "working_dir": f"workspace/{pid}",
            },
        },
        registry=registry,
    )
    if not tool_result.ok:
        message = tool_result.error.message if tool_result.error else "run_command failed"
        return {
            "ok": False,
            "passed": False,
            "error": message,
            "command": spec.display,
            "path": path,
            "expected_exit_code": spec.expected_exit_code,
        }

    data = tool_result.data or {}
    exit_code = int(data.get("exit_code", -1))
    passed = exit_code == spec.expected_exit_code
    return {
        "ok": True,
        "passed": passed,
        "exit_code": exit_code,
        "expected_exit_code": spec.expected_exit_code,
        "command": spec.display,
        "path": path,
        "stdout": data.get("stdout", ""),
        "stderr": data.get("stderr", ""),
    }


def snapshot_plan_fingerprints(session: object, paths: AgentPaths, project_id: str) -> None:
    """Store the PROJECT.md fingerprint on plan confirm."""
    meta = session.meta  # type: ignore[attr-defined]
    meta.project_doc_fingerprint = project_doc_fingerprint(project_dir(paths, project_id) / "PROJECT.md")



_DOC_LIST_SKIP_DIRS = frozenset({
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".cursor",
    ".plan-agent",
    ".agent",
    "_trash",
})


def _catalog_skips_rel(rel: str) -> bool:
    from tools.listing_privacy import path_is_internal_bookkeeping

    parts = rel.replace("\\", "/").split("/")
    if any(part in _DOC_LIST_SKIP_DIRS for part in parts[:-1]):
        return True
    return path_is_internal_bookkeeping(rel)


def _iter_project_markdown(root: Path):
    """Yield markdown files under *root*, skipping vendor/cache directories."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if name in _DOC_LIST_SKIP_DIRS:
                    continue
                stack.append(entry)
                continue
            if entry.suffix.lower() == ".md":
                yield entry


def _resolve_project_doc_path(
    root: Path,
    doc_path: str,
    *,
    add_md: bool = False,
) -> tuple[str, Path]:
    raw = str(doc_path or "").replace("\\", "/").strip().lstrip("/")
    if not raw or raw in {".", ".."}:
        raise ProjectModeError("文档路径无效")
    if add_md and not raw.lower().endswith(".md"):
        raw = f"{raw}.md"
    root_resolved = root.resolve()
    full = (root / raw).resolve()
    try:
        rel = full.relative_to(root_resolved).as_posix()
    except ValueError as exc:
        raise ProjectModeError(f"路径超出项目目录：{doc_path}") from exc
    if rel in {".", ""}:
        raise ProjectModeError("文档路径无效")
    if _catalog_skips_rel(rel):
        raise ProjectModeError("该路径不在项目文档目录中")
    if not rel.lower().endswith(".md"):
        raise ProjectModeError("只能操作 Markdown 文档")
    if Path(rel).name.lower() == ".md":
        raise ProjectModeError("文档名不能为空")
    return rel, full


def _write_project_markdown(full: Path, content: str) -> str:
    from evolve_tool_io import normalize_newlines, write_utf8_text

    text = normalize_newlines(content)
    write_utf8_text(full, text)
    return text


def _rel_from_title(old_rel: str, title: str) -> str:
    raw = str(title or "").replace("\\", "/").strip().strip("/")
    if not raw or raw in {".", ".."}:
        raise ProjectModeError("文档名不能为空")
    if "/" in raw:
        rel = raw
    else:
        parent = Path(old_rel).parent.as_posix()
        rel = raw if parent in {".", ""} else f"{parent}/{raw}"
    if not rel.lower().endswith(".md"):
        rel += ".md"
    return rel


def list_project_docs(paths: AgentPaths, project_id: str) -> list[dict[str, Any]]:
    """List project-root markdown files, skipping vendor/cache trees.

    Untouched scaffold placeholders (light or standard) stay hidden so an
    ordinary empty project does not look like a pile of template docs.
    Use ``count_untouched_template_docs`` when the UI needs a collapse hint.
    """
    root = project_dir(paths, project_id)
    if not root.is_dir():
        return []
    pid = normalize_project_id(project_id)
    docs: list[dict[str, Any]] = []
    for fpath in sorted(_iter_project_markdown(root), key=lambda item: item.as_posix().lower()):
        try:
            rel = fpath.relative_to(root).as_posix()
        except ValueError:
            continue
        if _catalog_skips_rel(rel):
            continue
        if "/" not in rel:
            try:
                body = fpath.read_text(encoding="utf-8")
            except OSError:
                body = ""
            if _is_template_document(rel, pid, body):
                continue
        try:
            st = fpath.stat()
            size = st.st_size
            mtime = float(st.st_mtime)
        except OSError:
            size = 0
            mtime = 0.0
        docs.append({
            "path": rel,
            "name": fpath.name,
            "size": size,
            "mtime": mtime,
            "is_standard": rel in PROJECT_ARTIFACTS,
        })
    return docs


def count_untouched_template_docs(paths: AgentPaths, project_id: str) -> int:
    """How many root-level scaffold placeholders are currently hidden from the catalog."""
    root = project_dir(paths, project_id)
    if not root.is_dir():
        return 0
    pid = normalize_project_id(project_id)
    count = 0
    for fpath in _iter_project_markdown(root):
        try:
            rel = fpath.relative_to(root).as_posix()
        except ValueError:
            continue
        if "/" in rel or _catalog_skips_rel(rel):
            continue
        try:
            body = fpath.read_text(encoding="utf-8")
        except OSError:
            body = ""
        if _is_template_document(rel, pid, body):
            count += 1
    return count


def _doc_content_hash(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def read_project_doc(paths: AgentPaths, project_id: str, doc_path: str) -> dict[str, Any]:
    """Read a single .md file from the project directory. Returns {type, path, content}."""
    root = project_dir(paths, project_id)
    safe_path, full = _resolve_project_doc_path(root, doc_path)
    if not full.is_file():
        raise ProjectModeError(f"找不到文档：{doc_path}")
    content = full.read_text(encoding="utf-8")
    try:
        mtime = float(full.stat().st_mtime)
    except OSError:
        mtime = 0.0
    return {
        "type": "project.doc.read.done",
        "path": safe_path,
        "content": content,
        "size": len(content),
        "mtime": mtime,
        "content_hash": _doc_content_hash(content),
    }


def create_project_doc(paths: AgentPaths, project_id: str, doc_path: str, content: str = "") -> dict[str, Any]:
    """Create a new .md file in the project directory."""
    root = project_dir(paths, project_id)
    safe_path, full = _resolve_project_doc_path(root, doc_path, add_md=True)
    if full.exists():
        raise ProjectModeError(f"已有同名文档：{safe_path}")
    full.parent.mkdir(parents=True, exist_ok=True)
    default_content = content if content else f"# {full.stem}\n\n"
    _write_project_markdown(full, default_content)
    return {
        "type": "project.doc.create.done",
        "path": safe_path,
        "name": full.name,
    }


def write_project_doc(
    paths: AgentPaths,
    project_id: str,
    doc_path: str,
    content: str,
    *,
    base_mtime: float | None = None,
    base_hash: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Overwrite an existing project markdown document.

    When ``base_mtime`` or ``base_hash`` is provided and the on-disk file no
    longer matches, raise ``ProjectModeError`` with a conflict payload unless
    ``force`` is true.
    """
    root = project_dir(paths, project_id)
    safe_path, full = _resolve_project_doc_path(root, doc_path)
    if not full.is_file():
        raise ProjectModeError(f"找不到文档：{doc_path}")
    current = full.read_text(encoding="utf-8")
    try:
        current_mtime = float(full.stat().st_mtime)
    except OSError:
        current_mtime = 0.0
    current_hash = _doc_content_hash(current)
    if not force:
        mismatched = False
        if base_hash is not None and str(base_hash).strip() and str(base_hash).strip() != current_hash:
            mismatched = True
        elif base_mtime is not None:
            try:
                if abs(float(base_mtime) - current_mtime) > 0.001:
                    mismatched = True
            except (TypeError, ValueError):
                mismatched = True
        if mismatched:
            raise ProjectModeError(
                "文档已被修改",
                code="doc_conflict",
                details={
                    "path": safe_path,
                    "mtime": current_mtime,
                    "content_hash": current_hash,
                    "size": len(current),
                },
            )
    written = _write_project_markdown(full, content)
    try:
        new_mtime = float(full.stat().st_mtime)
    except OSError:
        new_mtime = current_mtime
    return {
        "type": "project.doc.write.done",
        "path": safe_path,
        "name": full.name,
        "size": len(written),
        "mtime": new_mtime,
        "content_hash": _doc_content_hash(written),
    }


def rename_project_doc(
    paths: AgentPaths,
    project_id: str,
    doc_path: str,
    *,
    title: str = "",
    new_path: str = "",
) -> dict[str, Any]:
    """Rename a project markdown file by title (stem) or explicit new path."""
    root = project_dir(paths, project_id)
    old_rel, old_full = _resolve_project_doc_path(root, doc_path)
    if not old_full.is_file():
        raise ProjectModeError(f"找不到文档：{doc_path}")
    target_raw = str(new_path or "").strip() or _rel_from_title(old_rel, title)
    new_rel, new_full = _resolve_project_doc_path(root, target_raw, add_md=True)
    if new_rel == old_rel:
        return {
            "type": "project.doc.rename.done",
            "path": new_rel,
            "old_path": old_rel,
            "name": new_full.name,
        }
    if new_full.exists():
        raise ProjectModeError(f"已有同名文档：{new_rel}")
    new_full.parent.mkdir(parents=True, exist_ok=True)
    old_full.rename(new_full)
    return {
        "type": "project.doc.rename.done",
        "path": new_rel,
        "old_path": old_rel,
        "name": new_full.name,
    }


def _project_trash_dir(root: Path) -> Path:
    return root / "_trash"


def _unique_trash_target(trash_root: Path, source: Path) -> Path:
    candidate = trash_root / source.name
    if not candidate.exists():
        return candidate
    stem = source.stem if source.suffix else source.name
    suffix = source.suffix
    index = 1
    while True:
        alt = trash_root / f"{stem}-{index}{suffix}"
        if not alt.exists():
            return alt
        index += 1


def delete_project_doc(paths: AgentPaths, project_id: str, doc_path: str) -> dict[str, Any]:
    """Move a project markdown file into the project ``_trash`` folder (undoable)."""
    root = project_dir(paths, project_id)
    safe_path, full = _resolve_project_doc_path(root, doc_path)
    if not full.is_file():
        raise ProjectModeError(f"找不到文档：{doc_path}")
    content = full.read_text(encoding="utf-8")
    trash_root = _project_trash_dir(root)
    trash_root.mkdir(parents=True, exist_ok=True)
    target = _unique_trash_target(trash_root, full)
    full.rename(target)
    try:
        trash_rel = target.relative_to(root).as_posix()
    except ValueError:
        trash_rel = target.name
    return {
        "type": "project.doc.delete.done",
        "path": safe_path,
        "name": full.name,
        "is_standard": safe_path in PROJECT_ARTIFACTS,
        "trash_path": trash_rel,
        "undo": {
            "kind": "restore_doc",
            "path": safe_path,
            "trash_path": trash_rel,
            "content": content,
        },
    }


def restore_project_doc_from_trash(
    paths: AgentPaths,
    project_id: str,
    *,
    path: str,
    trash_path: str = "",
    content: str = "",
) -> dict[str, Any]:
    """Restore a document deleted via ``delete_project_doc``."""
    root = project_dir(paths, project_id)
    safe_path, full = _resolve_project_doc_path(root, path, add_md=True)
    if full.exists():
        raise ProjectModeError(f"已有同名文档：{safe_path}")
    restored = False
    if trash_path:
        trash_rel = str(trash_path or "").replace("\\", "/").strip().lstrip("/")
        trash_full = None
        if trash_rel:
            candidate = (root / trash_rel).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                candidate = None
            if candidate is not None and candidate.is_file():
                trash_full = candidate
        if trash_full is not None:
            full.parent.mkdir(parents=True, exist_ok=True)
            trash_full.rename(full)
            restored = True
    if not restored:
        full.parent.mkdir(parents=True, exist_ok=True)
        _write_project_markdown(full, content if isinstance(content, str) else "")
    return {
        "type": "project.doc.restore.done",
        "path": safe_path,
        "name": full.name,
    }



_SOURCE_EXTS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".kt", ".scala",
    ".css", ".scss", ".html", ".md", ".json", ".yaml", ".yml", ".toml",
})


def detect_potential_project(paths: AgentPaths, current_project_id: str = "") -> dict[str, Any] | None:
    """Scan workspace for recently-modified dirs that look like projects.

    Returns {type: project.detect, project_id, reason, file_count} or None.
    Only detects directories NOT already bound to the current session.
    """
    root = paths.workspace
    if not root.is_dir():
        return None

    import time
    now = time.time()
    cutoff = now - 600  # 10 minutes

    candidates: list[tuple[str, float, int]] = []  # (name, mtime, files)

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".") or name == _TEMPLATE_DIRNAME:
            continue
        if name == current_project_id:
            continue

        # Count source files under this directory (shallow scan, max depth 3)
        file_count = 0
        max_mtime = entry.stat().st_mtime

        try:
            for sub in entry.rglob("*"):
                if sub.is_file():
                    if sub.suffix.lower() in _SOURCE_EXTS or sub.name.endswith(".md"):
                        file_count += 1
                    try:
                        mt = sub.stat().st_mtime
                        if mt > max_mtime:
                            max_mtime = mt
                    except OSError:
                        pass
                if file_count > 20:  # early cutoff
                    break
        except OSError:
            pass

        if file_count >= 2 and max_mtime >= cutoff:
            candidates.append((name, max_mtime, file_count))

    if not candidates:
        return None

    # Pick the most recently modified candidate
    candidates.sort(key=lambda x: x[1], reverse=True)
    best = candidates[0]

    reason = (
        f"检测到 workspace/{best[0]} 下有 {best[2]} 个源文件，可以创建项目"
    )

    return {
        "type": "project.detect",
        "project_id": best[0],
        "reason": reason,
        "file_count": best[2],
    }


def _demo() -> None:
    paths = AgentPaths.discover()
    ensure_template(paths)
    print("[PASS] ensure_template")

    pid = "project-mode-demo"
    dest = project_dir(paths, pid)
    if dest.is_dir():
        shutil.rmtree(dest)
    create_project(paths, pid)
    assert (dest / "PROJECT.md").is_file()
    print("[PASS] create_project")

    root = project_root_rel(pid)
    reason = project_mode_block_reason(
        active_shell="project",
        project_root=root,
        plan_status="draft",
        tool_name="run_evolved",
        arguments={"tool_name": "run_command", "arguments": {"command": "echo hi", "working_dir": root}},
    )
    assert reason is None
    print("[PASS] no plan gate: draft project may run commands")

    reason3 = project_mode_block_reason(
        active_shell="project",
        project_root=root,
        plan_status="confirmed",
        tool_name="run_evolved",
        arguments={"tool_name": "write_evolve", "arguments": {}},
    )
    assert reason3 and "write_evolve" in reason3
    print("[PASS] write_evolve blocked in project")

    reason_clone_evolve = project_mode_block_reason(
        active_shell="project",
        project_root=root,
        plan_status="confirmed",
        tool_name="run_evolved",
        arguments={
            "tool_name": "git_clone",
            "arguments": {
                "url": "https://github.com/github/gitignore.git",
                "target": "evolve_tools",
                "dest": "evolve/tools/common/foo",
            },
        },
    )
    assert reason_clone_evolve and "evolve/tools" in reason_clone_evolve
    print("[PASS] git_clone evolve_tools blocked in project shell")

    shutil.rmtree(dest, ignore_errors=True)
    print("[PASS] project_mode demo")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    else:
        for item_id in list_projects(AgentPaths.discover()):
            print(item_id)
