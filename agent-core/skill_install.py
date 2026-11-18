"""Safe, approval-gated creation/import of directory Skills.

Imported instructions and resources are data only: this module never executes them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote, urljoin, urlparse

from skill_registry import SkillRegistry, SkillRegistryError

MAX_FILE_BYTES = 1 * 1024 * 1024
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_FILES = 256
MAX_SKILL_MD_BYTES = 64 * 1024
FETCH_TIMEOUT_SECONDS = 15.0
MAX_NETWORK_SECONDS = 90.0
MAX_REDIRECTS = 5
_SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class SkillInstallError(ValueError):
    """A Skill source, package, approval, or publication failed safely."""


class SkillFetchProvider(Protocol):
    def fetch(self, url: str, *, timeout: float, max_bytes: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class SkillFileSummary:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class SkillInstallPreview:
    preview_id: str
    source: Mapping[str, Any]
    skill_id: str
    name: str
    target: str
    derived_fields: Mapping[str, str]
    skill_md: str
    files: tuple[SkillFileSummary, ...]
    content_sha256: str
    _root: str
    _root_identity: tuple[int, int]
    _prepared_files: tuple[_PreparedFile, ...]

    def approval_payload(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "source": dict(self.source),
            "skill_id": self.skill_id,
            "name": self.name,
            "target": self.target,
            "derived_fields": dict(self.derived_fields),
            "skill_md": self.skill_md,
            "files": [
                {"path": item.path, "size": item.size, "sha256": item.sha256}
                for item in self.files
            ],
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True)
class SkillInstallResult:
    """Outcome fields; status is explicit and residual flags remaining data.

    Statuses: installed (discovered), publish_failed (may be partial),
    installed_refresh_failed, installed_not_discoverable, or
    installed_stage_cleanup_failed.
    """

    status: str
    skill_id: str
    target: str
    content_sha256: str
    discovered: bool
    message: str
    residual: bool = False


def prepare_skill_install(
    *,
    skills_root: str | Path,
    source: Mapping[str, Any],
    provider: SkillFetchProvider | None = None,
) -> SkillInstallPreview:
    """Fetch/normalize/validate a package without publishing it.

    Source contracts:
      * {"kind":"content", "files": {relative_path: str|bytes}}
      * {"kind":"github", "owner", "repo", "ref", "path"}
      * {"kind":"https", "url": "https://.../SKILL.md"}

    ``skills_root`` must be supplied by the trusted host, never copied from model
    arguments. For self-evolution workers it must be the host-bound candidate root,
    never the stable root. Injected network providers must honor timeout/max_bytes,
    use public HTTPS only, revalidate every redirect hop, and reject private/metadata
    destinations. The default provider follows ``tools/builtin/fetch_url`` policy.
    """
    root = _trusted_root(skills_root)
    root_info = root.lstat()
    root_identity = (root_info.st_dev, root_info.st_ino)
    if not isinstance(source, Mapping):
        raise SkillInstallError("source must be a mapping")
    kind = source.get("kind")
    try:
        if kind == "content":
            raw_files = _content_files(source)
            source_summary = {"kind": "content"}
        elif kind == "github":
            raw_files, source_summary = _github_files(source, provider, deadline=time.monotonic() + MAX_NETWORK_SECONDS)
        elif kind == "https":
            raw_files, source_summary = _https_skill_file(source, provider, deadline=time.monotonic() + MAX_NETWORK_SECONDS)
        else:
            raise SkillInstallError("unsupported source kind; expected content, github, or https")
        files = _normalize_files(raw_files)
        skill_md, skill_id, name, derived = _convert_and_validate(files)
        files = tuple(
            _PreparedFile(item.path, skill_md.encode("utf-8") if item.path == "SKILL.md" else item.content)
            for item in files
        )
        summaries = tuple(
            SkillFileSummary(item.path, len(item.content), hashlib.sha256(item.content).hexdigest())
            for item in files
        )
        content_hash = _package_digest(summaries)
        # Parse the exact normalized bytes in an isolated throwaway directory using
        # the production Registry, not a parallel approximation.
        with tempfile.TemporaryDirectory(prefix="skill-install-validate-") as temp:
            staged = Path(temp) / skill_id
            _write_package(staged, files)
            registry = SkillRegistry.discover(Path(temp))
            package = registry.load(skill_id)
            if package.descriptor.name != name:
                raise SkillInstallError("registry name differs from normalized skill name")
        return SkillInstallPreview(
            preview_id=uuid.uuid4().hex,
            source=MappingProxyType(dict(source_summary)),
            skill_id=skill_id,
            name=name,
            target=str(root / skill_id),
            derived_fields=MappingProxyType(derived),
            skill_md=skill_md,
            files=summaries,
            content_sha256=content_hash,
            _root=str(root),
            _root_identity=root_identity,
            _prepared_files=files,
        )
    except SkillInstallError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError, SkillRegistryError) as exc:
        raise SkillInstallError(_safe_error(exc)) from exc


def commit_skill_install(
    preview: SkillInstallPreview,
    *,
    approval: Callable[[dict[str, Any]], bool],
    binding_check: Callable[[], bool] | None = None,
) -> SkillInstallResult:
    """Publish one exact prepared package after host policy approval.

    The injected callback owns the existing approval preference policy. This
    module does not force a modal confirmation; only exact ``True`` authorizes.
    """
    if not isinstance(preview, SkillInstallPreview):
        raise SkillInstallError("invalid install preview")
    if not callable(approval):
        raise SkillInstallError("approval callback is required")
    root = _trusted_root(preview._root)
    root_info = root.lstat()
    if (root_info.st_dev, root_info.st_ino) != preview._root_identity:
        raise SkillInstallError("host skills root identity changed after preparation")
    if str(root / preview.skill_id) != preview.target:
        raise SkillInstallError("preview target binding changed")
    if _package_digest(preview.files) != preview.content_sha256:
        raise SkillInstallError("preview content digest mismatch")
    if tuple((f.path, hashlib.sha256(f.content).hexdigest()) for f in preview._prepared_files) != tuple(
        (item.path, item.sha256) for item in preview.files
    ):
        raise SkillInstallError("prepared content no longer matches preview")
    target = root / preview.skill_id
    if os.path.lexists(target):
        raise SkillInstallError("skill target already exists; overwrite is not supported")
    if binding_check is not None and (not callable(binding_check) or binding_check() is not True):
        raise SkillInstallError("host binding changed before approval")

    staging: Path | None = None
    staging_identity: tuple[int, int] | None = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=f".skill-install-{preview.preview_id}-", dir=root))
        stage_info = staging.lstat()
        if _is_link_or_reparse(stage_info) or not stat.S_ISDIR(stage_info.st_mode):
            raise SkillInstallError("staging root is a link/reparse point or not a directory")
        staging_identity = (stage_info.st_dev, stage_info.st_ino)
        _write_package(staging / preview.skill_id, preview._prepared_files)
        # Revalidate on the exact same-volume staging tree that will be published.
        staged_registry = SkillRegistry.discover(staging)
        staged_package = staged_registry.load(preview.skill_id)
        if staged_package.descriptor.content_hash == "":
            raise SkillInstallError("staged package failed registry validation")
        shown = preview.approval_payload()
        shown_digest = _approval_digest(shown)
        if approval(shown) is not True:
            raise SkillInstallError("skill installation was not approved")
        if _approval_digest(shown) != shown_digest:
            raise SkillInstallError("approval preview was modified during confirmation")
        if binding_check is not None and binding_check() is not True:
            raise SkillInstallError("host binding changed after approval")
        # The host callback may run arbitrary UI/event-pump code. Recheck staging
        # identity before/after scanning to reject replacement with symlink/junction.
        if not _root_matches(root, preview._root_identity):
            raise SkillInstallError("host skills root identity changed during confirmation")
        if not _staging_root_matches(staging, staging_identity):
            raise SkillInstallError("staging root changed or became a Windows reparse point during confirmation")
        rescanned = _scan_package(staging / preview.skill_id)
        if not _staging_root_matches(staging, staging_identity):
            raise SkillInstallError("staging root changed during package rescan")
        if not _root_matches(root, preview._root_identity):
            raise SkillInstallError("host skills root identity changed during package rescan")
        if _package_digest(rescanned) != preview.content_sha256:
            raise SkillInstallError("staging changed during confirmation; preview binding no longer matches")
        if tuple((row.path, row.size, row.sha256) for row in rescanned) != tuple(
            (row.path, row.size, row.sha256) for row in preview.files
        ):
            raise SkillInstallError("staging file inventory changed during confirmation")
        staged_registry = SkillRegistry.discover(staging)
        staged_registry.load(preview.skill_id)
        if os.path.lexists(target):
            raise SkillInstallError("skill target already exists; overwrite is not supported")
        if not _root_matches(root, preview._root_identity):
            raise SkillInstallError("host skills root identity changed immediately before publication")
        try:
            # Publish from the immutable preview snapshot, not from a path the
            # confirmation callback could have changed after the rescan.
            _publish_no_replace(target, preview._prepared_files)
        except Exception as exc:
            residual = bool(getattr(exc, "residual", os.path.lexists(target)))
            message = f"publish_failed: {_safe_error(exc)}"
            if residual:
                message += f"; target may contain partial data and was preserved: {target}"
            if staging is not None and _cleanup_staging(staging, staging_identity):
                staging = None
            elif staging is not None:
                message += f"; staging was not recursively deleted because its identity/reparse state changed: {staging}"
                staging = None
            return SkillInstallResult(
                status="publish_failed", skill_id=preview.skill_id,
                target=str(target), content_sha256=preview.content_sha256,
                discovered=False, message=message, residual=residual,
            )
        if staging is not None and _cleanup_staging(staging, staging_identity):
            staging = None
        else:
            # The installed package is real, but cleanup could not safely remove
            # staging; report that state instead of a clean success.
            staging_path = str(staging)
            staging = None
            discovered_after_publish = False
            refresh_note = ""
            try:
                from skill_runtime import invalidate_skill_index_cache, get_skill_registry
                invalidate_skill_index_cache(root)
                refreshed = get_skill_registry(root)
                discovered_after_publish = preview.skill_id in {item.skill_id for item in refreshed.skills}
                if discovered_after_publish:
                    refreshed.load(preview.skill_id)
            except Exception as exc:
                refresh_note = f"; registry refresh also failed: {_safe_error(exc)}"
            return SkillInstallResult(
                status="installed_stage_cleanup_failed", skill_id=preview.skill_id,
                target=str(target), content_sha256=preview.content_sha256,
                discovered=discovered_after_publish,
                message=f"published, but staging was not removed because its identity/reparse state changed: {staging_path}{refresh_note}",
                residual=True,
            )
        try:
            published_files = _scan_package(target)
            if _package_digest(published_files) != preview.content_sha256:
                return SkillInstallResult(
                    status="publish_failed", skill_id=preview.skill_id,
                    target=str(target), content_sha256=preview.content_sha256,
                    discovered=False, residual=True,
                    message="publish_failed: target changed during publication; possible partial/concurrent data was preserved",
                )
            from skill_runtime import invalidate_skill_index_cache, get_skill_registry
            invalidate_skill_index_cache(root)
            registry = SkillRegistry.discover(root)
            discovered = any(item.skill_id == preview.skill_id for item in registry.skills)
            if discovered:
                registry.load(preview.skill_id)
                get_skill_registry(root).load(preview.skill_id)
        except Exception as exc:
            return SkillInstallResult(
                status="installed_refresh_failed", skill_id=preview.skill_id,
                target=str(target), content_sha256=preview.content_sha256,
                discovered=False, residual=True,
                message=f"published, but registry refresh failed; target data remains: {_safe_error(exc)}",
            )
        if not discovered:
            return SkillInstallResult(
                status="installed_not_discoverable", skill_id=preview.skill_id,
                target=str(target), content_sha256=preview.content_sha256,
                discovered=False, residual=True,
                message="published, but registry did not discover the Skill; target data remains",
            )
        return SkillInstallResult(
            status="installed", skill_id=preview.skill_id, target=str(target),
            content_sha256=preview.content_sha256, discovered=True,
            message="installed and discoverable",
        )
    except SkillInstallError:
        raise
    except Exception as exc:
        raise SkillInstallError(_safe_error(exc)) from exc
    finally:
        if staging is not None and not _cleanup_staging(staging, staging_identity):
            raise SkillInstallError(
                f"staging was not deleted because it changed identity or contains a link/reparse point: {staging}"
            )


_WINDOWS_REPARSE_POINT = 0x400


def _is_link_or_reparse(info: os.stat_result) -> bool:
    attrs = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attrs & _WINDOWS_REPARSE_POINT)


def _root_matches(path: Path, identity: tuple[int, int]) -> bool:
    try:
        info = path.lstat()
        return (not _is_link_or_reparse(info) and stat.S_ISDIR(info.st_mode)
                and (info.st_dev, info.st_ino) == identity)
    except OSError:
        return False


def _staging_root_matches(path: Path, identity: tuple[int, int] | None) -> bool:
    if identity is None:
        return False
    try:
        info = path.lstat()
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            return False
        if (info.st_dev, info.st_ino) != identity:
            return False
        is_junction = getattr(path, "is_junction", None)
        return not (callable(is_junction) and is_junction())
    except OSError:
        return False


def _tree_has_link_or_reparse(root: Path) -> bool:
    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            for name in (*dirs, *files):
                if _is_link_or_reparse((Path(current) / name).lstat()):
                    return True
        return False
    except OSError:
        return True


def _cleanup_staging(path: Path, identity: tuple[int, int] | None) -> bool:
    # Never recurse into a replaced path, symlink, junction, or reparse point.
    if not _staging_root_matches(path, identity) or _tree_has_link_or_reparse(path):
        return False
    # Narrow the replacement window after traversal. A portable Windows path API
    # cannot hold/revalidate every ancestor via an open directory handle here.
    if not _staging_root_matches(path, identity):
        return False
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return not os.path.lexists(path)


def _trusted_root(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise SkillInstallError("host skills root must be a filesystem path")
    requested_root = Path(value).absolute()
    try:
        requested_info = requested_root.lstat()
        if _is_link_or_reparse(requested_info) or not stat.S_ISDIR(requested_info.st_mode):
            raise SkillInstallError("host skills root must be a real directory, not a link/reparse point")
        root = requested_root.resolve(strict=True)
        root_info = root.lstat()
    except OSError as exc:
        raise SkillInstallError("host skills root is unavailable") from exc
    if _is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise SkillInstallError("host skills root must be a real directory, not a link/reparse point")
    return root


def _content_files(source: Mapping[str, Any]) -> Mapping[str, bytes | str]:
    if set(source) != {"kind", "files"} or not isinstance(source.get("files"), Mapping):
        raise SkillInstallError("content source must contain only a files mapping")
    return source["files"]


def _normalize_files(raw: Mapping[str, bytes | str]) -> tuple[_PreparedFile, ...]:
    if not isinstance(raw, Mapping) or not raw or len(raw) > MAX_FILES:
        raise SkillInstallError(f"package must contain 1..{MAX_FILES} files")
    result: list[_PreparedFile] = []
    total = 0
    seen: set[str] = set()
    for raw_path, value in raw.items():
        if not isinstance(raw_path, str):
            raise SkillInstallError("package file path must be text")
        path = _safe_relative_path(raw_path)
        folded = path.casefold()
        if folded in seen:
            raise SkillInstallError(f"duplicate package path: {path}")
        seen.add(folded)
        if not isinstance(value, (bytes, str)):
            raise SkillInstallError(f"unsupported file content type: {path}")
        content = value.encode("utf-8") if isinstance(value, str) else value
        if len(content) > MAX_FILE_BYTES:
            raise SkillInstallError(f"file exceeds size limit: {path}")
        total += len(content)
        if total > MAX_PACKAGE_BYTES:
            raise SkillInstallError("package exceeds total size limit")
        result.append(_PreparedFile(path, content))
    result.sort(key=lambda item: item.path)
    if "SKILL.md" not in {item.path for item in result}:
        raise SkillInstallError("package is missing SKILL.md")
    return tuple(result)


def _safe_relative_path(raw: str) -> str:
    if not raw or "\x00" in raw or "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise SkillInstallError(f"unsafe package path: {raw!r}")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SkillInstallError(f"unsafe package path: {raw!r}")
    if any(part.endswith((".", " ")) or ":" in part for part in parts):
        raise SkillInstallError(f"unsafe package path: {raw!r}")
    if any(part.casefold() == ".git" for part in parts):
        raise SkillInstallError("Git metadata cannot be imported as Skill resources")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if any(part.split(".", 1)[0].casefold() in reserved for part in parts):
        raise SkillInstallError(f"unsafe Windows device path: {raw!r}")
    return str(PurePosixPath(*parts))


def _convert_and_validate(files: tuple[_PreparedFile, ...]) -> tuple[str, str, str, dict[str, str]]:
    raw = next(item.content for item in files if item.path == "SKILL.md")
    if len(raw) > MAX_SKILL_MD_BYTES:
        raise SkillInstallError("SKILL.md exceeds registry body size limit")
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeError as exc:
        raise SkillInstallError("SKILL.md must be valid UTF-8") from exc
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillInstallError("invalid SKILL.md: missing YAML frontmatter")
    raw_frontmatter, body = match.groups()
    keys: dict[str, list[str]] = {}
    for line in raw_frontmatter.splitlines():
        if line and not line[0].isspace() and ":" in line and not line.lstrip().startswith("#"):
            key, _ = line.split(":", 1)
            keys.setdefault(key.strip(), []).append(line)
    for required in ("name", "description"):
        if len(keys.get(required, [])) != 1:
            raise SkillInstallError(f"external Skill must declare exactly one {required}")
    name_line = keys["name"][0].split(":", 1)[1].strip().strip("\"'")
    description_value = keys["description"][0].split(":", 1)[1].strip().strip("\"'")
    if description_value in {"|", ">", "|-", ">-", "|+", ">+"}:
        raise SkillInstallError("multiline YAML description is unsupported by the project registry parser")
    if not description_value or len(description_value) > 1024:
        raise SkillInstallError("description must contain 1..1024 characters")
    if not _SKILL_ID_RE.fullmatch(name_line):
        raise SkillInstallError(f"invalid skill name for project registry: {name_line!r}")
    generated = {
        "triggers": "[]",
        "allowed_tools": "[]",
        "risk_level": "unassessed",
        "verification": '"Not verified: imported Skill instructions are unreviewed"',
        "version": '"0.0.0-unverified"',
    }
    lines_to_add: list[str] = []
    derived: dict[str, str] = {}
    for key, value in generated.items():
        if key not in keys:
            lines_to_add.append(f"{key}: {value}")
            derived[key] = value.strip('"')
        elif len(keys[key]) != 1:
            raise SkillInstallError(f"duplicate project metadata field: {key}")
    # The external standard defines compatibility as a string, while the project
    # parser expects a mapping. Preserve the original under a non-reserved key.
    frontmatter_lines = raw_frontmatter.splitlines()
    frontmatter_lines = [
        re.sub(r"^([ \t]*)compatibility(?=\s*:)", r"\1external_compatibility", line)
        if re.match(r"^compatibility\s*:\s*.+$", line) else line
        for line in frontmatter_lines
    ]
    converted = "---\n" + "\n".join(frontmatter_lines).rstrip() + "\n" + "\n".join(lines_to_add) + "\n---\n" + body
    try:
        # Use the stable registry's public API; avoid source-branch private helpers.
        with tempfile.TemporaryDirectory(prefix="skill-install-registry-check-") as temp:
            validation_root = Path(temp)
            skill_dir = validation_root / name_line
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(converted, encoding="utf-8")
            package = SkillRegistry.discover(validation_root).load(name_line)
        descriptor = package.descriptor
        if not package.body.strip():
            raise SkillInstallError("SKILL.md body must contain instructions")
    except SkillRegistryError as exc:
        raise SkillInstallError(f"registry validation failed: {exc}") from exc
    if derived:
        derived_names = ", ".join(sorted(derived))
        converted = converted.replace(
            "\n---\n",
            f'\nproject_compatibility: "derived-defaults-v1"\nproject_compatibility_fields: "{derived_names}"\n---\n',
            1,
        )
    return converted, descriptor.skill_id, descriptor.name, derived


def _write_package(destination: Path, files: tuple[_PreparedFile, ...]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for item in files:
        target = destination.joinpath(*item.path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(target, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(item.content)
            stream.flush()
            os.fsync(stream.fileno())


class _PublishError(OSError):
    def __init__(self, message: str, *, residual: bool) -> None:
        super().__init__(message)
        self.residual = residual


def _publish_no_replace(target: Path, files: tuple[_PreparedFile, ...]) -> None:
    """Reserve the target exclusively and create files with O_EXCL, SKILL.md last.

    This avoids the replace-empty-directory behavior of POSIX rename and makes a
    racing existing target a hard failure on Windows and POSIX alike.
    """
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise _PublishError("target appeared before exclusive publish; no overwrite attempted", residual=False) from exc
    target_identity = target.lstat()
    if _is_link_or_reparse(target_identity) or not stat.S_ISDIR(target_identity.st_mode):
        raise _PublishError("exclusive target reservation is not a plain directory", residual=os.path.lexists(target))
    created_dirs: list[tuple[Path, int, int]] = [(target, target_identity.st_dev, target_identity.st_ino)]
    created_files: list[tuple[Path, int, int, bytes]] = []
    try:
        ordered = tuple(item for item in files if item.path != "SKILL.md") + tuple(
            item for item in files if item.path == "SKILL.md"
        )
        for item in ordered:
            target_now = target.lstat()
            if (_is_link_or_reparse(target_now) or not stat.S_ISDIR(target_now.st_mode)
                    or (target_now.st_dev, target_now.st_ino) != (target_identity.st_dev, target_identity.st_ino)):
                raise SkillInstallError("publish target directory identity changed")
            parts = item.path.split("/")
            parent = target
            for part in parts[:-1]:
                parent = parent / part
                if not os.path.lexists(parent):
                    parent.mkdir(exist_ok=False)
                    info = parent.lstat()
                    created_dirs.append((parent, info.st_dev, info.st_ino))
                info = parent.lstat()
                if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                    raise SkillInstallError("publish path changed to a link or non-directory")
            path = parent / parts[-1]
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(path, flags, 0o600)
            info = os.fstat(fd)
            created_files.append((path, info.st_dev, info.st_ino, item.content))
            with os.fdopen(fd, "wb") as stream:
                stream.write(item.content)
                stream.flush()
                os.fsync(stream.fileno())
            target_now = target.lstat()
            if (_is_link_or_reparse(target_now) or not stat.S_ISDIR(target_now.st_mode)
                    or (target_now.st_dev, target_now.st_ino) != (target_identity.st_dev, target_identity.st_ino)):
                raise SkillInstallError("publish target directory changed during file creation")
    except Exception as exc:
        _rollback_published(created_files, created_dirs)
        residual = bool(os.path.lexists(target))
        raise _PublishError(_safe_error(exc), residual=residual) from exc


def _rollback_published(
    files: list[tuple[Path, int, int, bytes]], dirs: list[tuple[Path, int, int]],
) -> None:
    for path, device, inode, expected_bytes in reversed(files):
        try:
            info = path.lstat()
            if (_is_link_or_reparse(info) is False and stat.S_ISREG(info.st_mode)
                    and info.st_dev == device and info.st_ino == inode):
                current = path.read_bytes()
                if current == expected_bytes or expected_bytes.startswith(current):
                    path.unlink()
        except OSError:
            pass
    for path, device, inode in reversed(dirs):
        try:
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and not _is_link_or_reparse(info) and info.st_dev == device and info.st_ino == inode:
                path.rmdir()
        except OSError:
            # Preserve any unexpected/concurrent data rather than recurse-delete.
            pass


def _package_digest(files: tuple[SkillFileSummary, ...]) -> str:
    canonical = "".join(f"{item.path}:{item.sha256}\n" for item in sorted(files, key=lambda row: row.path))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _approval_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scan_package(root: Path) -> tuple[SkillFileSummary, ...]:
    try:
        root_stat = root.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or _is_link_or_reparse(root_stat):
            raise SkillInstallError("staging package root is not a real directory")
        summaries: list[SkillFileSummary] = []
        total = 0
        for current, dirs, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in tuple(dirs):
                path = current_path / name
                info = path.lstat()
                if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                    raise SkillInstallError(f"staging contains a link or non-directory: {path.name}")
            for name in names:
                path = current_path / name
                info = path.lstat()
                if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
                    raise SkillInstallError(f"staging contains a link or special file: {path.name}")
                if info.st_nlink != 1:
                    raise SkillInstallError(f"staging contains a hard-linked file: {path.name}")
                rel = path.relative_to(root).as_posix()
                _safe_relative_path(rel)
                if info.st_size > MAX_FILE_BYTES:
                    raise SkillInstallError(f"staging file exceeds limit: {rel}")
                total += info.st_size
                if total > MAX_PACKAGE_BYTES or len(summaries) >= MAX_FILES:
                    raise SkillInstallError("staging package exceeds resource limits")
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                summaries.append(SkillFileSummary(rel, info.st_size, digest))
        summaries.sort(key=lambda item: item.path)
        return tuple(summaries)
    except SkillInstallError:
        raise
    except OSError as exc:
        raise SkillInstallError("could not safely rescan staged package") from exc


def _github_files(source: Mapping[str, Any], provider: SkillFetchProvider | None, *, deadline: float):
    if set(source) != {"kind", "owner", "repo", "ref", "path"}:
        raise SkillInstallError("GitHub source requires exactly owner, repo, ref, and path")
    owner, repo, ref = (source.get(k) for k in ("owner", "repo", "ref"))
    if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in (owner, repo)):
        raise SkillInstallError("invalid GitHub repository identity")
    if not isinstance(ref, str) or len(ref) > 200:
        raise SkillInstallError("invalid GitHub ref")
    ref_resolution = "explicit"
    if not ref.strip():
        metadata_url = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        metadata_payload = _fetch(provider, metadata_url, 256 * 1024, deadline=deadline)
        try:
            metadata = json.loads(metadata_payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SkillInstallError("GitHub repository metadata response is invalid") from exc
        if not isinstance(metadata, dict):
            raise SkillInstallError("GitHub repository metadata response is malformed")
        default_branch = metadata.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip() or len(default_branch) > 200:
            raise SkillInstallError("GitHub repository did not provide a valid default_branch")
        ref = default_branch.strip()
        ref_resolution = "default_branch"
    raw_subdir = source.get("path", "")
    if not isinstance(raw_subdir, str):
        raise SkillInstallError("GitHub path must be text; use an empty string for repository root")
    subdir = _safe_relative_path(raw_subdir) if raw_subdir else ""
    if any("?" in v or "#" in v for v in (owner, repo, ref)):
        raise SkillInstallError("invalid GitHub source identity")
    api = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/git/trees/{quote(ref, safe='')}?recursive=1"
    tree_payload = _fetch(provider, api, MAX_PACKAGE_BYTES, deadline=deadline)
    try:
        tree = json.loads(tree_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SkillInstallError("GitHub tree response is invalid") from exc
    if not isinstance(tree, dict) or tree.get("truncated") is True or not isinstance(tree.get("tree"), list):
        raise SkillInstallError("GitHub tree is truncated or malformed; import refused")
    prefix = subdir.rstrip("/") + "/" if subdir else ""
    selected: list[tuple[str, Mapping[str, Any]]] = []
    for entry in tree["tree"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise SkillInstallError("GitHub tree entry is malformed")
        path = entry["path"]
        if subdir:
            if path == subdir or not path.startswith(prefix):
                continue
            relative = path[len(prefix):]
        else:
            relative = path
        safe = _safe_relative_path(relative)
        if entry.get("type") == "tree":
            continue
        mode = entry.get("mode")
        if entry.get("type") != "blob" or mode not in {"100644", "100755"}:
            raise SkillInstallError(f"unsupported link, hardlink, or special file in Skill package: {safe}")
        size = entry.get("size")
        if type(size) is not int or size < 0 or size > MAX_FILE_BYTES:
            raise SkillInstallError(f"invalid or oversized GitHub file: {safe}")
        selected.append((safe, entry))
    if len(selected) > MAX_FILES:
        raise SkillInstallError("GitHub package exceeds file-count limit")
    if sum(int(entry["size"]) for _, entry in selected) > MAX_PACKAGE_BYTES:
        raise SkillInstallError("GitHub package exceeds total size limit")
    files: dict[str, bytes] = {}
    raw = f"https://raw.githubusercontent.com/{quote(owner, safe='')}/{quote(repo, safe='')}/{quote(ref, safe='/')}"
    if subdir:
        raw += "/" + quote(subdir, safe="/")
    for path, entry in selected:
        url = raw + "/" + "/".join(quote(part, safe="") for part in path.split("/"))
        payload = _fetch(provider, url, min(MAX_FILE_BYTES, int(entry["size"]) + 1), deadline=deadline)
        if len(payload) != entry["size"]:
            raise SkillInstallError(f"GitHub file size mismatch: {path}")
        expected_sha = entry.get("sha")
        actual_sha = hashlib.sha1(b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload).hexdigest()
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", expected_sha) or actual_sha != expected_sha:
            raise SkillInstallError(f"GitHub content changed or did not match selected tree: {path}")
        files[path] = payload
    tree_sha = tree.get("sha")
    if not isinstance(tree_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", tree_sha):
        raise SkillInstallError("GitHub tree response has no valid immutable tree identity")
    return files, {
        "kind": "github", "owner": owner, "repo": repo, "ref": ref,
        "ref_resolution": ref_resolution, "tree_sha": tree_sha, "path": subdir,
    }


def _https_skill_file(source: Mapping[str, Any], provider: SkillFetchProvider | None, *, deadline: float):
    if set(source) != {"kind", "url"} or not isinstance(source.get("url"), str):
        raise SkillInstallError("HTTPS source requires one SKILL.md URL")
    url = source["url"].strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SkillInstallError("SKILL.md source must be a credential-free HTTPS URL")
    data = _fetch(provider, url, MAX_SKILL_MD_BYTES + 1, deadline=deadline)
    if len(data) > MAX_SKILL_MD_BYTES:
        raise SkillInstallError("SKILL.md exceeds registry body size limit")
    text = data.decode("utf-8-sig", errors="strict")
    if re.search(r"(?i)(?:\]\(|\]\s*\(|`)(?:\.?\.?/)?(?:scripts|references|assets)/", text):
        raise SkillInstallError("direct SKILL.md references package resources; import the complete package instead")
    query_redacted = bool(parsed.query)
    return {"SKILL.md": data}, {
        "kind": "https", "url": _redact_url(url),
        "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        "query_redacted": query_redacted,
        "query_notice": (
            "Query string hidden because it may contain credentials."
            if query_redacted else ""
        ),
    }


def _fetch(provider: SkillFetchProvider | None, url: str, max_bytes: int, *, deadline: float) -> bytes:
    if max_bytes < 1 or max_bytes > MAX_PACKAGE_BYTES + 1:
        raise SkillInstallError("invalid fetch size limit")
    timeout = min(FETCH_TIMEOUT_SECONDS, deadline - time.monotonic())
    if timeout <= 0:
        raise SkillInstallError("skill import exceeded total network timeout")
    try:
        if provider is not None:
            method = getattr(provider, "fetch", None)
            if not callable(method):
                raise SkillInstallError("provider must implement fetch(url, timeout=, max_bytes=)")
            data = method(url, timeout=timeout, max_bytes=max_bytes)
        else:
            data = _fetch_public_url(url, max_bytes=max_bytes, timeout=timeout)
    except SkillInstallError:
        raise
    except Exception as exc:
        raise SkillInstallError(f"provider fetch failed: {_safe_error(exc)}") from exc
    if not isinstance(data, bytes):
        raise SkillInstallError("fetch provider must return bytes")
    if len(data) > max_bytes:
        raise SkillInstallError("download exceeds configured size limit")
    return data


def _fetch_public_url(url: str, *, max_bytes: int, timeout: float = FETCH_TIMEOUT_SECONDS) -> bytes:
    # Reuse fetch_url's public-host and DNS/IP policy; validate every redirect hop.
    try:
        import httpx
        from tools.builtin.fetch_url import _assert_host_allowed, _normalize_http_url
        from tools.http_client import make_httpx_client
        current = _normalize_http_url(url)
        if urlparse(current).scheme != "https" or urlparse(current).username or urlparse(current).password:
            raise SkillInstallError("only credential-free HTTPS downloads are supported")
        with make_httpx_client(timeout=timeout) as client:
            for hop in range(MAX_REDIRECTS + 1):
                parsed = urlparse(current)
                if parsed.username or parsed.password:
                    raise SkillInstallError("credential-bearing redirect refused")
                _assert_host_allowed(parsed.hostname or "")
                with client.stream("GET", current, headers={"User-Agent": "my-agent-skill-import/1.0"}, follow_redirects=False) as response:
                    if response.is_redirect:
                        if hop == MAX_REDIRECTS:
                            raise SkillInstallError("too many redirects")
                        location = response.headers.get("location")
                        if not location:
                            raise SkillInstallError("redirect has no location")
                        current = _normalize_http_url(urljoin(current, location))
                        if urlparse(current).scheme != "https":
                            raise SkillInstallError("redirect to non-HTTPS URL refused")
                        continue
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise SkillInstallError("download exceeds configured size limit")
                        chunks.append(chunk)
                    return b"".join(chunks)
    except SkillInstallError:
        raise
    except Exception as exc:
        raise SkillInstallError(f"public HTTPS fetch failed: {_safe_error(exc)}") from exc
    raise SkillInstallError("too many redirects")


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(netloc=parsed.hostname or "", query="", fragment="").geturl()


def _safe_error(exc: Exception) -> str:
    # Do not expose request URLs (which may contain tokens/query credentials) in logs/errors.
    text = str(exc)
    text = re.sub(r"https?://[^\s'\"]+", "<redacted-url>", text)
    return text[:500] or type(exc).__name__
