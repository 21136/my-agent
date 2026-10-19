"""Read-only scanning and explicit in-place adoption of external projects."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paths import AgentPaths, read_agent_state_payload, write_agent_state_payload
from project_mode import normalize_project_id

EXTERNAL_PROJECTS_KEY = "external_projects"

_MARKER_NAMES = (
    ".git",
    "README.md",
    "PROJECT.md",
    "SCOPE.md",
    "DESIGN.md",
    "TECH-DESIGN.md",
    "TASKS.md",
    "VERIFY.md",
    "RELEASE.md",
    "package.json",
    "pyproject.toml",
    "pom.xml",
    "Cargo.toml",
    ".env",
)


class ExternalProjectError(ValueError):
    """The external project cannot be safely scanned or adopted."""


@dataclass(frozen=True, slots=True)
class ExternalProjectScan:
    external_root: str
    project_id: str
    logical_root: str
    markers: tuple[str, ...]
    has_git: bool
    git_dirty: bool | None
    has_verify: bool
    has_readme: bool
    has_env: bool
    has_build_manifest: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_root": self.external_root,
            "project_id": self.project_id,
            "logical_root": self.logical_root,
            "markers": list(self.markers),
            "has_git": self.has_git,
            "git_dirty": self.git_dirty,
            "has_verify": self.has_verify,
            "has_readme": self.has_readme,
            "has_env": self.has_env,
            "has_build_manifest": self.has_build_manifest,
            "requires_confirmation": True,
        }


@dataclass(frozen=True, slots=True)
class ExternalProjectBinding:
    project_id: str
    external_root: str
    logical_root: str
    healthy: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "external_root": self.external_root,
            "logical_root": self.logical_root,
            "healthy": self.healthy,
        }


def _canonical_root(raw_root: str) -> Path:
    raw = str(raw_root or "").strip().strip('"')
    if not raw:
        raise ExternalProjectError("外部项目路径不能为空")
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ExternalProjectError(f"外部项目路径不可访问：{raw}") from exc
    if not resolved.is_dir():
        raise ExternalProjectError(f"外部项目路径不是目录：{resolved}")
    return resolved


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_external_root(paths: AgentPaths, root: Path) -> None:
    agent_root = paths.agent_root.resolve()
    protected = (
        agent_root,
        paths.workspace.resolve(),
        paths.data.resolve(),
        (paths.data / "sessions").resolve(),
    )
    # Reject the agent root, its parents, and protected descendants. This
    # prevents a junction from exposing the agent itself or session storage.
    for protected_root in protected:
        if _within(root, protected_root) or _within(protected_root, root):
            raise ExternalProjectError(f"禁止接入受保护目录：{root}")


def _derive_project_id(root: Path, raw: str | None) -> str:
    candidate = str(raw or "").strip() or root.name
    try:
        return normalize_project_id(candidate)
    except Exception:
        cleaned = re.sub(r"[^a-z0-9-]+", "-", candidate.casefold()).strip("-")
        if not cleaned or not re.match(r"^[a-z]", cleaned):
            digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
            cleaned = f"imported-{digest}"
        return normalize_project_id(cleaned[:64].rstrip("-"))


def _git_dirty(root: Path) -> bool | None:
    git_dir = root / ".git"
    if not git_dir.exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def scan_external_project(
    paths: AgentPaths,
    raw_root: str,
    *,
    project_id: str | None = None,
) -> ExternalProjectScan:
    root = _canonical_root(raw_root)
    _validate_external_root(paths, root)
    pid = _derive_project_id(root, project_id)
    logical = paths.workspace / pid
    markers = tuple(name for name in _MARKER_NAMES if (root / name).exists())
    return ExternalProjectScan(
        external_root=str(root),
        project_id=pid,
        logical_root=f"workspace/{pid}",
        markers=markers,
        has_git=(root / ".git").exists(),
        git_dirty=_git_dirty(root),
        has_verify=(root / "VERIFY.md").is_file(),
        has_readme=(root / "README.md").is_file(),
        has_env=(root / ".env").is_file(),
        has_build_manifest=any(
            (root / name).is_file()
            for name in ("package.json", "pyproject.toml", "pom.xml", "Cargo.toml")
        ),
    )


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            if bool(is_junction()):
                return True
        except OSError:
            pass
    return path.is_symlink() or os.path.islink(path)


def _create_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        raise ExternalProjectError("外部项目原地接入当前只支持 Windows junction")
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return
    except OSError as first_error:
        # Directory junctions do not require copying source files and are
        # available on Windows without requiring the Python symlink privilege.
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or str(first_error)).strip()
        raise ExternalProjectError(f"无法创建项目 junction：{detail}") from first_error


def _read_bindings(paths: AgentPaths) -> dict[str, dict[str, str]]:
    raw = read_agent_state_payload(paths).get(EXTERNAL_PROJECTS_KEY, {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for pid, value in raw.items():
        if not isinstance(pid, str) or not isinstance(value, dict):
            continue
        external = value.get("external_root")
        logical = value.get("logical_root")
        if isinstance(external, str) and isinstance(logical, str):
            out[pid] = {"external_root": external, "logical_root": logical}
    return out


def _write_binding(paths: AgentPaths, binding: ExternalProjectBinding) -> None:
    payload = read_agent_state_payload(paths)
    bindings = _read_bindings(paths)
    bindings[binding.project_id] = {
        "external_root": binding.external_root,
        "logical_root": binding.logical_root,
    }
    payload[EXTERNAL_PROJECTS_KEY] = bindings
    write_agent_state_payload(paths, payload)


def _remove_binding(paths: AgentPaths, project_id: str) -> None:
    payload = read_agent_state_payload(paths)
    bindings = _read_bindings(paths)
    bindings.pop(project_id, None)
    if bindings:
        payload[EXTERNAL_PROJECTS_KEY] = bindings
    else:
        payload.pop(EXTERNAL_PROJECTS_KEY, None)
    write_agent_state_payload(paths, payload)


def list_external_bindings(paths: AgentPaths) -> dict[str, ExternalProjectBinding]:
    out: dict[str, ExternalProjectBinding] = {}
    for pid in _read_bindings(paths):
        binding = validate_external_binding(paths, pid)
        if binding is not None:
            out[pid] = binding
    return out


def resolve_external_project_path(paths: AgentPaths, raw: str) -> tuple[Path, str] | None:
    """If *raw* (agent-relative or workspace-relative) targets a mapped external project,
    return ``(resolved_path, agent_relative_posix)``.
    """
    norm = raw.strip().replace("\\", "/").lstrip("/")
    if not norm:
        return None
    bindings = _read_bindings(paths)
    if not bindings:
        return None

    pid = ""
    sub = ""
    if norm.startswith("workspace/"):
        rest = norm[len("workspace/") :]
        parts = rest.split("/", 1)
        pid = parts[0]
        sub = parts[1] if len(parts) > 1 else ""
    else:
        parts = norm.split("/", 1)
        if parts[0] in bindings:
            pid = parts[0]
            sub = parts[1] if len(parts) > 1 else ""

    if not pid or pid not in bindings:
        return None
    entry = bindings[pid]
    ext_raw = entry.get("external_root")
    if not isinstance(ext_raw, str) or not ext_raw.strip():
        return None
    try:
        ext_root = Path(ext_raw).resolve(strict=True)
    except OSError:
        return None
    target = (ext_root / sub).resolve() if sub else ext_root
    if not _within(target, ext_root):
        return None
    rel_posix = f"workspace/{pid}/{sub}".rstrip("/") if sub else f"workspace/{pid}"
    return target, rel_posix


def to_external_agent_relative(paths: AgentPaths, resolved: Path) -> str | None:
    """If *resolved* is inside an adopted external project root, return ``workspace/<id>/...``."""
    bindings = _read_bindings(paths)
    if not bindings:
        return None
    target = resolved.resolve()
    for pid, entry in bindings.items():
        ext_raw = entry.get("external_root")
        if not isinstance(ext_raw, str) or not ext_raw.strip():
            continue
        try:
            ext_root = Path(ext_raw).resolve(strict=True)
        except OSError:
            continue
        if _within(target, ext_root):
            rel = target.relative_to(ext_root).as_posix()
            return f"workspace/{pid}" if rel == "." else f"workspace/{pid}/{rel}"
    return None


def apply_external_project(
    paths: AgentPaths,
    scan: ExternalProjectScan,
) -> ExternalProjectBinding:
    root = _canonical_root(scan.external_root)
    _validate_external_root(paths, root)
    pid = _derive_project_id(root, scan.project_id)
    if pid != scan.project_id:
        raise ExternalProjectError("导入扫描结果已过期，请重新扫描")
    logical = paths.workspace / pid
    if os.path.lexists(logical):
        raise ExternalProjectError(f"项目逻辑目录已存在：workspace/{pid}")
    paths.workspace.mkdir(parents=True, exist_ok=True)
    junction_created = False
    try:
        _create_junction(logical, root)
        junction_created = True
    except ExternalProjectError:
        # Fallback for exFAT / FAT32 volumes where Windows junctions (WinError 1) are unsupported:
        # use logical path mapping in state.json without requiring an OS reparse point.
        junction_created = False
    try:
        if junction_created and logical.resolve(strict=True) != root:
            raise ExternalProjectError("junction 目标校验失败")
        binding = ExternalProjectBinding(
            project_id=pid,
            external_root=str(root),
            logical_root=f"workspace/{pid}",
        )
        _write_binding(paths, binding)
        return binding
    except Exception:
        try:
            if junction_created and _is_link_or_junction(logical):
                os.rmdir(logical)
        except OSError:
            pass
        raise


def validate_external_binding(
    paths: AgentPaths,
    project_id: str,
) -> ExternalProjectBinding | None:
    try:
        pid = normalize_project_id(project_id)
    except Exception:
        return None
    entry = _read_bindings(paths).get(pid)
    if not entry:
        return None
    try:
        external = _canonical_root(entry["external_root"])
        logical = paths.workspace / pid
        if os.path.lexists(logical):
            if not _is_link_or_junction(logical):
                return None
            if logical.resolve(strict=True) != external:
                return None
    except (OSError, ExternalProjectError):
        return None
    return ExternalProjectBinding(pid, str(external), f"workspace/{pid}", healthy=True)


def detach_external_project(paths: AgentPaths, project_id: str) -> None:
    try:
        pid = normalize_project_id(project_id)
    except Exception as exc:
        raise ExternalProjectError(f"无效项目 ID：{project_id}") from exc
    entry = _read_bindings(paths).get(pid)
    if not entry:
        return
    logical = paths.workspace / pid
    if os.path.lexists(logical):
        if not _is_link_or_junction(logical):
            raise ExternalProjectError(f"拒绝解除非 junction 目录：{logical}")
        os.rmdir(logical)
    _remove_binding(paths, pid)
