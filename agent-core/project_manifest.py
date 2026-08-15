"""Document manifest and freshness propagation for Desktop projects."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from paths import AgentPaths

MANIFEST_FILENAME = "manifest.json"
CHG_LEDGER_FILENAME = "changes.jsonl"
MANIFEST_SCHEMA_VERSION = "0.1"
VALID_TIERS = frozenset({"small", "normal", "large"})
VALID_STATUSES = frozenset({"current", "stale_soft", "stale", "evidence_stale"})
STANDARD_ARTIFACTS = (
    "PROJECT.md",
    "SCOPE.md",
    "DESIGN.md",
    "TECH-DESIGN.md",
    "TASKS.md",
    "VERIFY.md",
    "RELEASE.md",
)
SIDECAR_ARTIFACTS = ("ENV.md", "MAP.md", "TASKS.archive.md")
ARTIFACT_ROLES = {
    "PROJECT.md": "project",
    "SCOPE.md": "scope",
    "DESIGN.md": "design",
    "TECH-DESIGN.md": "tech_design",
    "TASKS.md": "tasks",
    "VERIFY.md": "verify",
    "RELEASE.md": "release",
    "ENV.md": "environment",
    "MAP.md": "code_map",
    "TASKS.archive.md": "task_archive",
}
ARTIFACT_DEPENDENCIES = {
    "PROJECT.md": (),
    "SCOPE.md": ("PROJECT.md",),
    "DESIGN.md": ("SCOPE.md",),
    "TECH-DESIGN.md": ("DESIGN.md",),
    "TASKS.md": ("TECH-DESIGN.md",),
    "VERIFY.md": ("TASKS.md",),
    "RELEASE.md": ("VERIFY.md",),
    "ENV.md": (),
    "MAP.md": (),
    "TASKS.archive.md": ("TASKS.md",),
}
ARTIFACT_REQUIRED_FOR = {
    "PROJECT.md": ("requirements",),
    "SCOPE.md": ("requirements", "design", "implementation"),
    "DESIGN.md": ("design", "implementation"),
    "TECH-DESIGN.md": ("design", "implementation"),
    "TASKS.md": ("requirements", "design", "implementation", "verification", "release"),
    "VERIFY.md": ("verification", "release"),
    "RELEASE.md": ("release",),
    "ENV.md": ("implementation", "verification"),
    "MAP.md": (),
    "TASKS.archive.md": (),
}
_ID_RE = re.compile(r"\b(?:REQ|AC|UX|TD|ADR|T|V|REL|CHG|IT|S)-\d{3,}\b")
_REVISION_RE = re.compile(r"^r(\d+)$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_CHANGE_ID_RE = re.compile(r"^CHG-\d{3,}$")
CHANGE_LEDGER_FIELDS = (
    "change_id",
    "adopted_at",
    "source",
    "proposal_id",
    "paths",
    "summary",
    "requirements",
    "tasks",
    "acceptance",
    "verification",
    "stale_docs",
    "replan_required",
    "before_revision",
    "after_revision",
)


class ManifestError(ValueError):
    """Invalid or unreadable project manifest."""


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def manifest_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".plan-agent" / MANIFEST_FILENAME


def change_ledger_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".plan-agent" / CHG_LEDGER_FILENAME


def project_manifest_path(paths: AgentPaths, project_id: str) -> Path:
    return manifest_path(paths.workspace / _project_id(project_id))


def _project_id(project_id: str) -> str:
    value = str(project_id or "").strip().lower().replace("_", "-")
    if not _PROJECT_ID_RE.fullmatch(value):
        raise ManifestError(f"invalid project id: {project_id!r}")
    return value


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_entry(name: str, root: Path, *, tier: str, revision: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": name,
        "role": ARTIFACT_ROLES[name],
        "revision": revision,
        "status": "current",
        "tier": tier,
        "depends_on": list(ARTIFACT_DEPENDENCIES[name]),
        "last_adopted_change": None,
        "required_for": list(ARTIFACT_REQUIRED_FOR[name]),
    }
    digest = _sha256(root / name)
    if digest is not None:
        entry["content_sha256"] = digest
        entry["ids"] = sorted(set(_ID_RE.findall((root / name).read_text(encoding="utf-8"))))
    return entry


def build_manifest(
    project_root: Path | str,
    project_id: str,
    *,
    tier: str = "normal",
    revision: str = "r0",
    now: str | None = None,
) -> dict[str, Any]:
    pid = _project_id(project_id)
    if tier not in VALID_TIERS:
        raise ManifestError(f"invalid tier: {tier!r}")
    if not _REVISION_RE.fullmatch(revision):
        raise ManifestError(f"invalid revision: {revision!r}")
    root = Path(project_root)
    artifacts = [
        _artifact_entry(name, root, tier=tier, revision=revision)
        for name in STANDARD_ARTIFACTS
    ]
    for name in SIDECAR_ARTIFACTS:
        if (root / name).is_file():
            artifacts.append(_artifact_entry(name, root, tier=tier, revision=revision))
    timestamp = now or utc_now_iso()
    result = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project": {"id": pid, "root": f"workspace/{pid}", "tier": tier},
        "manifest_revision": revision,
        "created_at": timestamp,
        "updated_at": timestamp,
        "artifacts": artifacts,
    }
    validate_manifest(result)
    return result


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise ManifestError("manifest root must be an object")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestError("unsupported manifest schema_version")
    project = manifest.get("project")
    if not isinstance(project, Mapping):
        raise ManifestError("manifest project must be an object")
    pid = project.get("id")
    if not isinstance(pid, str) or not _PROJECT_ID_RE.fullmatch(pid):
        raise ManifestError("manifest project.id is invalid")
    if project.get("root") != f"workspace/{pid}":
        raise ManifestError("manifest project.root is invalid")
    if project.get("tier") not in VALID_TIERS:
        raise ManifestError("manifest project.tier is invalid")
    manifest_revision = manifest.get("manifest_revision", "r0")
    if not isinstance(manifest_revision, str) or not _REVISION_RE.fullmatch(manifest_revision):
        raise ManifestError("manifest_revision is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ManifestError("manifest artifacts must be an array")
    by_path: dict[str, Mapping[str, Any]] = {}
    for item in artifacts:
        if not isinstance(item, Mapping):
            raise ManifestError("manifest artifact must be an object")
        name = item.get("path")
        if name not in ARTIFACT_ROLES or name in by_path:
            raise ManifestError(f"invalid or duplicate artifact path: {name!r}")
        by_path[name] = item
        required = {
            "path",
            "role",
            "revision",
            "status",
            "tier",
            "depends_on",
            "last_adopted_change",
            "required_for",
        }
        if not required.issubset(item):
            missing = sorted(required - set(item))
            raise ManifestError(f"artifact {name!r} missing fields: {', '.join(missing)}")
        if item.get("role") != ARTIFACT_ROLES[name]:
            raise ManifestError(f"artifact {name!r} has an invalid role")
        if not isinstance(item.get("revision"), str) or not _REVISION_RE.fullmatch(item["revision"]):
            raise ManifestError(f"artifact {name!r} has an invalid revision")
        if item.get("status") not in VALID_STATUSES:
            raise ManifestError(f"artifact {name!r} has an invalid status")
        if item.get("tier") not in VALID_TIERS:
            raise ManifestError(f"artifact {name!r} has an invalid tier")
        if not isinstance(item.get("depends_on"), list) or not all(
            dep in ARTIFACT_ROLES for dep in item["depends_on"]
        ):
            raise ManifestError(f"artifact {name!r} has invalid dependencies")
        if item.get("last_adopted_change") is not None and not re.fullmatch(
            r"CHG-\d{3,}", str(item["last_adopted_change"])
        ):
            raise ManifestError(f"artifact {name!r} has an invalid change id")
        if "content_sha256" in item and (
            not isinstance(item["content_sha256"], str) or not _HASH_RE.fullmatch(item["content_sha256"])
        ):
            raise ManifestError(f"artifact {name!r} has an invalid content_sha256")
    missing_standard = [name for name in STANDARD_ARTIFACTS if name not in by_path]
    if missing_standard:
        raise ManifestError(f"manifest missing standard artifacts: {', '.join(missing_standard)}")


def load_manifest(path: Path | str) -> dict[str, Any] | None:
    manifest_file = Path(path)
    if not manifest_file.is_file():
        return None
    try:
        value = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {manifest_file}: {exc}") from exc
    validate_manifest(value)
    return dict(value)


def save_manifest(path: Path | str, manifest: Mapping[str, Any]) -> Path:
    validate_manifest(manifest)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def load_change_ledger(path: Path | str) -> list[dict[str, Any]]:
    ledger_file = Path(path)
    if not ledger_file.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = ledger_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ManifestError(f"cannot read change ledger {ledger_file}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"invalid change ledger line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ManifestError(f"change ledger line {line_number} must be an object")
        missing = [field for field in CHANGE_LEDGER_FIELDS if field not in value]
        if missing:
            raise ManifestError(
                f"change ledger line {line_number} missing fields: {', '.join(missing)}"
            )
        if not _CHANGE_ID_RE.fullmatch(str(value["change_id"])):
            raise ManifestError(f"change ledger line {line_number} has an invalid change_id")
        if not isinstance(value["paths"], list) or not all(
            isinstance(item, str) for item in value["paths"]
        ):
            raise ManifestError(f"change ledger line {line_number} has invalid paths")
        for field in ("requirements", "tasks", "acceptance", "verification", "stale_docs"):
            if not isinstance(value[field], list) or not all(
                isinstance(item, str) for item in value[field]
            ):
                raise ManifestError(f"change ledger line {line_number} has invalid {field}")
        if not isinstance(value["replan_required"], bool):
            raise ManifestError(
                f"change ledger line {line_number} has invalid replan_required"
            )
        entries.append(value)
    return entries


def next_change_id(project_root: Path | str) -> str:
    entries = load_change_ledger(change_ledger_path(project_root))
    highest = 0
    for entry in entries:
        match = re.fullmatch(r"CHG-(\d+)", str(entry.get("change_id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"CHG-{highest + 1:03d}"


def append_change_ledger(project_root: Path | str, change: Mapping[str, Any]) -> dict[str, Any]:
    entry = dict(change)
    missing = [field for field in CHANGE_LEDGER_FIELDS if field not in entry]
    if missing:
        raise ManifestError(f"change entry missing fields: {', '.join(missing)}")
    if not _CHANGE_ID_RE.fullmatch(str(entry["change_id"])):
        raise ManifestError("change_id must match CHG-NNN")
    if not isinstance(entry["paths"], list) or not all(
        isinstance(item, str) for item in entry["paths"]
    ):
        raise ManifestError("change paths must be a list of strings")
    for field in ("requirements", "tasks", "acceptance", "verification", "stale_docs"):
        if not isinstance(entry[field], list) or not all(
            isinstance(item, str) for item in entry[field]
        ):
            raise ManifestError(f"change {field} must be a list of strings")
    if not isinstance(entry["replan_required"], bool):
        raise ManifestError("replan_required must be a boolean")
    ledger_file = change_ledger_path(project_root)
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    with ledger_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    return entry


def bootstrap_manifest(
    project_root: Path | str,
    project_id: str,
    *,
    tier: str = "normal",
    revision: str = "r0",
) -> dict[str, Any]:
    manifest = build_manifest(project_root, project_id, tier=tier, revision=revision)
    save_manifest(manifest_path(project_root), manifest)
    return manifest


def ensure_project_manifest(
    paths: AgentPaths,
    project_id: str,
    *,
    tier: str = "normal",
) -> dict[str, Any]:
    root = paths.workspace / _project_id(project_id)
    target = manifest_path(root)
    existing = load_manifest(target)
    if existing is None:
        from project_mode import migrate_legacy_project

        migrate_legacy_project(paths, project_id)
        return bootstrap_manifest(root, project_id, tier=tier)
    changed = refresh_manifest(existing, root)
    if changed:
        save_manifest(target, existing)
    return existing


def _status_rank(status: str) -> int:
    return {"current": 0, "evidence_stale": 1, "stale_soft": 2, "stale": 3}.get(status, 3)


def _mark_status(entry: dict[str, Any], status: str) -> None:
    if _status_rank(status) > _status_rank(str(entry.get("status", "current"))):
        entry["status"] = status


def _normalize_paths(paths: str | Path | list[str | Path] | tuple[str | Path, ...]) -> list[str]:
    if isinstance(paths, (str, Path)):
        paths = [paths]
    result: list[str] = []
    for value in paths:
        name = str(value).replace("\\", "/").strip("/").split("/")[-1]
        if name in ARTIFACT_ROLES and name not in result:
            result.append(name)
    return result


def _dependents(manifest: Mapping[str, Any], changed: set[str]) -> set[str]:
    result = set(changed)
    changed_again = True
    while changed_again:
        changed_again = False
        for item in manifest.get("artifacts", []):
            if not isinstance(item, dict):
                continue
            if item.get("path") in result:
                continue
            if any(dep in result for dep in item.get("depends_on", [])):
                result.add(str(item["path"]))
                changed_again = True
    return result


def propagate_stale(
    manifest: dict[str, Any],
    changed_paths: str | Path | list[str | Path] | tuple[str | Path, ...],
    *,
    level: str = "L2",
) -> dict[str, Any]:
    """Mark changed artifacts and their dependents stale according to L1/L2."""
    normalized_level = str(level).upper()
    if normalized_level not in {"L1", "L2"}:
        raise ManifestError("stale propagation level must be L1 or L2")
    changed = set(_normalize_paths(changed_paths))
    target_status = "stale_soft" if normalized_level == "L1" else "stale"
    affected = _dependents(manifest, changed)
    for item in manifest.get("artifacts", []):
        if isinstance(item, dict) and item.get("path") in affected:
            _mark_status(item, target_status)
    manifest["updated_at"] = utc_now_iso()
    return manifest


def mark_evidence_stale(
    manifest: dict[str, Any],
    affected_paths: str | Path | list[str | Path] | tuple[str | Path, ...] | None = None,
) -> dict[str, Any]:
    """Mark verification evidence stale without changing document revisions."""
    names = set(_normalize_paths(affected_paths or []))
    if names:
        names.update(name for name in STANDARD_ARTIFACTS if name in names)
    else:
        names = {"VERIFY.md", "RELEASE.md"}
    if names & set(ARTIFACT_ROLES) - {"VERIFY.md", "RELEASE.md"}:
        names.update({"VERIFY.md", "RELEASE.md"})
    for item in manifest.get("artifacts", []):
        if isinstance(item, dict) and item.get("path") in names:
            _mark_status(item, "evidence_stale")
    manifest["updated_at"] = utc_now_iso()
    return manifest


def _default_external_level(name: str) -> str:
    return "L2" if name in {"PROJECT.md", "SCOPE.md", "TECH-DESIGN.md"} else "L1"


def refresh_manifest(
    manifest: dict[str, Any],
    project_root: Path | str,
    *,
    change_levels: Mapping[str, str] | None = None,
    evidence_changed: bool = False,
) -> bool:
    """Detect disk edits, retain revisions, and propagate freshness changes."""
    root = Path(project_root)
    changed: dict[str, str] = {}
    for item in manifest.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("path") or "")
        digest = _sha256(root / name)
        old_digest = item.get("content_sha256")
        if digest == old_digest:
            continue
        if digest is None:
            item.pop("content_sha256", None)
            item.pop("ids", None)
        else:
            item["content_sha256"] = digest
            item["ids"] = sorted(set(_ID_RE.findall((root / name).read_text(encoding="utf-8"))))
        changed[name] = (change_levels or {}).get(name, _default_external_level(name))
    for level in ("L1", "L2"):
        paths = [name for name, item_level in changed.items() if item_level.upper() == level]
        if paths:
            propagate_stale(manifest, paths, level=level)
    if evidence_changed:
        mark_evidence_stale(manifest)
    if changed or evidence_changed:
        manifest["updated_at"] = utc_now_iso()
    return bool(changed or evidence_changed)


def refresh_project_manifest(
    paths: AgentPaths,
    project_id: str,
    *,
    change_levels: Mapping[str, str] | None = None,
    evidence_changed: bool = False,
) -> dict[str, Any]:
    root = paths.workspace / _project_id(project_id)
    target = manifest_path(root)
    manifest = ensure_project_manifest(paths, project_id)
    if refresh_manifest(
        manifest,
        root,
        change_levels=change_levels,
        evidence_changed=evidence_changed,
    ):
        save_manifest(target, manifest)
    return manifest


def adopt_manifest_change(
    manifest: dict[str, Any],
    project_root: Path | str,
    changed_paths: str | Path | list[str | Path] | tuple[str | Path, ...],
    *,
    change_id: str | None = None,
    level: str = "L2",
) -> dict[str, Any]:
    """Create a new revision for adopted source files and stale their dependents."""
    if change_id is not None and not re.fullmatch(r"CHG-\d{3,}", change_id):
        raise ManifestError("change_id must match CHG-NNN")
    names = _normalize_paths(changed_paths)
    current = manifest.get("manifest_revision", "r0")
    match = _REVISION_RE.fullmatch(str(current))
    if not match:
        raise ManifestError("manifest_revision is invalid")
    next_revision = f"r{int(match.group(1)) + 1}"
    selected = set(names)
    root = Path(project_root)
    for item in manifest.get("artifacts", []):
        if not isinstance(item, dict) or item.get("path") not in selected:
            continue
        item["revision"] = next_revision
        item["status"] = "current"
        digest = _sha256(root / str(item["path"]))
        if digest is None:
            item.pop("content_sha256", None)
            item.pop("ids", None)
        else:
            item["content_sha256"] = digest
            item["ids"] = sorted(set(_ID_RE.findall((root / str(item["path"])).read_text(encoding="utf-8"))))
        item["last_adopted_change"] = change_id
    manifest["manifest_revision"] = next_revision
    propagate_stale(manifest, names, level=level)
    for item in manifest.get("artifacts", []):
        if isinstance(item, dict) and item.get("path") in selected:
            item["status"] = "current"
    manifest["updated_at"] = utc_now_iso()
    return manifest


def manifest_has_l2_stale(manifest: Mapping[str, Any]) -> bool:
    return any(
        isinstance(item, Mapping) and item.get("status") == "stale"
        for item in manifest.get("artifacts", [])
    )


def manifest_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached payload safe to attach to a WebSocket event."""
    return deepcopy(dict(manifest))
