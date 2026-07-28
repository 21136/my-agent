"""Host scope configuration (HOST-SCOPE.md, T-1002).

Loads ``data/host_scope.json``, validates host roots against agent root overlap,
and applies deny globs + system directory rules before host path resolution (T-1003).
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

from paths import AgentPaths, PathOutOfBoundsError, _is_within
from tools.schema import ToolErrorCode

HOST_SCOPE_FILENAME = "host_scope.json"
HOST_SCOPE_VERSION = 1
_HOST_URI_RE = re.compile(r"^host:([a-z][a-z0-9_-]*)(?:/(.*))?$", re.IGNORECASE)

DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    "**/.ssh/**",
    "**/.gnupg/**",
    "**/AppData/**",
    "**/.env",
    "**/.env.*",
    "**/*credentials*",
    "**/id_rsa",
    "**/id_rsa.pub",
    "**/node_modules/**",
)

_WINDOWS_SYSTEM_PREFIXES: tuple[str, ...] = (
    "C:/Windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
)


class HostScopeError(Exception):
    """Base error for host scope configuration."""


class HostScopeConfigError(HostScopeError):
    """Invalid or inconsistent host_scope.json."""


class HostPathDeniedError(HostScopeError):
    """Resolved path hits denylist or system_deny."""

    code = ToolErrorCode.PATH_DENIED

    def __init__(self, message: str, *, path: str, reason: str) -> None:
        super().__init__(message)
        self.path = path
        self.reason = reason


class HostRootNotFoundError(HostScopeError):
    """Unknown host:<id> in URI."""

    code = ToolErrorCode.NOT_FOUND

    def __init__(self, message: str, *, host_id: str, path: str) -> None:
        super().__init__(message)
        self.host_id = host_id
        self.path = path


class HostScopePermissionError(HostScopeError):
    """Host root lacks read or write permission for the operation."""

    code = ToolErrorCode.PERMISSION_DENIED

    def __init__(self, message: str, *, path: str, host_id: str) -> None:
        super().__init__(message)
        self.path = path
        self.host_id = host_id


@dataclass(frozen=True, slots=True)
class HostRoot:
    id: str
    path: Path
    label: str
    added_at: str
    read: bool = True
    write: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path.as_posix(),
            "label": self.label,
            "added_at": self.added_at,
            "read": self.read,
            "write": self.write,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> HostRoot:
        raw_id = payload.get("id")
        raw_path = payload.get("path")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise HostScopeConfigError("host_roots[].id must be a non-empty string")
        if not _HOST_ID_RE.fullmatch(raw_id):
            raise HostScopeConfigError(
                f"host_roots[].id invalid {raw_id!r}; use [a-z][a-z0-9_-]*"
            )
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise HostScopeConfigError("host_roots[].path must be a non-empty string")
        path = Path(raw_path).expanduser().resolve()
        label = payload.get("label")
        if not isinstance(label, str) or not label.strip():
            raise HostScopeConfigError("host_roots[].label must be a non-empty string")
        added_at = payload.get("added_at")
        if not isinstance(added_at, str) or not added_at.strip():
            raise HostScopeConfigError("host_roots[].added_at must be an ISO timestamp string")
        read = payload.get("read", True)
        write = payload.get("write", False)
        if not isinstance(read, bool) or not isinstance(write, bool):
            raise HostScopeConfigError("host_roots[].read/write must be booleans")
        if write and not read:
            raise HostScopeConfigError("host_roots[].write requires read=true")
        return cls(
            id=raw_id,
            path=path,
            label=label.strip(),
            added_at=added_at.strip(),
            read=read,
            write=write,
        )


_HOST_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass
class HostScopeConfig:
    version: int = HOST_SCOPE_VERSION
    host_roots: list[HostRoot] = field(default_factory=list)
    deny_globs: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_GLOBS))
    system_deny: bool = True
    wizard_completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "host_roots": [entry.to_dict() for entry in self.host_roots],
            "deny_globs": list(self.deny_globs),
            "system_deny": self.system_deny,
        }
        if self.wizard_completed:
            payload["wizard_completed"] = True
        return payload

    def host_by_id(self, host_id: str) -> HostRoot | None:
        for entry in self.host_roots:
            if entry.id == host_id:
                return entry
        return None


def host_scope_file(paths: AgentPaths) -> Path:
    return paths.data / HOST_SCOPE_FILENAME


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def empty_host_scope() -> HostScopeConfig:
    return HostScopeConfig()


def load_host_scope(
    paths: AgentPaths,
    *,
    create_if_missing: bool = False,
) -> HostScopeConfig:
    """Load host scope config; missing file yields empty roots unless *create_if_missing*."""
    path = host_scope_file(paths)
    if not path.is_file():
        if create_if_missing:
            paths.data.mkdir(parents=True, exist_ok=True)
            config = empty_host_scope()
            save_host_scope(paths, config)
            return config
        return empty_host_scope()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostScopeConfigError(f"invalid {HOST_SCOPE_FILENAME}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HostScopeConfigError(f"{HOST_SCOPE_FILENAME} root must be an object")
    return parse_host_scope_payload(payload)


def parse_host_scope_payload(payload: dict[str, Any]) -> HostScopeConfig:
    version = payload.get("version", HOST_SCOPE_VERSION)
    if version != HOST_SCOPE_VERSION:
        raise HostScopeConfigError(f"unsupported host_scope version: {version}")

    raw_roots = payload.get("host_roots", [])
    if not isinstance(raw_roots, list):
        raise HostScopeConfigError("host_roots must be an array")

    roots: list[HostRoot] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_roots):
        if not isinstance(item, dict):
            raise HostScopeConfigError(f"host_roots[{index}] must be an object")
        entry = HostRoot.from_dict(item)
        if entry.id in seen_ids:
            raise HostScopeConfigError(f"duplicate host_roots id: {entry.id!r}")
        seen_ids.add(entry.id)
        roots.append(entry)

    raw_deny = payload.get("deny_globs", list(DEFAULT_DENY_GLOBS))
    if not isinstance(raw_deny, list) or not all(isinstance(x, str) for x in raw_deny):
        raise HostScopeConfigError("deny_globs must be an array of strings")

    system_deny = payload.get("system_deny", True)
    if not isinstance(system_deny, bool):
        raise HostScopeConfigError("system_deny must be a boolean")

    wizard_completed = payload.get("wizard_completed", False)
    if not isinstance(wizard_completed, bool):
        raise HostScopeConfigError("wizard_completed must be a boolean")

    return HostScopeConfig(
        version=HOST_SCOPE_VERSION,
        host_roots=roots,
        deny_globs=list(raw_deny),
        system_deny=system_deny,
        wizard_completed=wizard_completed,
    )


def save_host_scope(paths: AgentPaths, config: HostScopeConfig) -> Path:
    paths.data.mkdir(parents=True, exist_ok=True)
    path = host_scope_file(paths)
    path.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def host_root_overlaps_agent(host_path: Path | str, agent_root: Path | str) -> bool:
    """True if *host_path* is agent root or a subdirectory of agent root (HOST-SCOPE S3)."""
    host = Path(host_path).expanduser().resolve()
    agent = Path(agent_root).resolve()
    return host == agent or _is_within(host, agent)


def validate_host_root_path(path: Path | str, *, agent_root: Path) -> Path:
    """Validate a filesystem path before registering as host root."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise HostScopeConfigError(f"host root path is not a directory: {resolved}")
    if host_root_overlaps_agent(resolved, agent_root):
        raise HostScopeConfigError(
            "host root cannot be agent root or inside agent root "
            f"(agent_root={agent_root.resolve().as_posix()})"
        )
    return resolved


def validate_host_scope(paths: AgentPaths, config: HostScopeConfig) -> None:
    """Re-validate all entries (existence + agent overlap + unique ids)."""
    seen: set[str] = set()
    for entry in config.host_roots:
        if entry.id in seen:
            raise HostScopeConfigError(f"duplicate host_roots id: {entry.id!r}")
        seen.add(entry.id)
        if not entry.path.is_dir():
            raise HostScopeConfigError(
                f"host root {entry.id!r} path missing or not a directory: {entry.path}"
            )
        if host_root_overlaps_agent(entry.path, paths.agent_root):
            raise HostScopeConfigError(
                f"host root {entry.id!r} overlaps agent root"
            )


def parse_host_uri(raw: str) -> tuple[str, str]:
    """Parse ``host:<id>/relative`` into (id, relative posix path)."""
    text = raw.strip()
    match = _HOST_URI_RE.fullmatch(text)
    if not match:
        raise ValueError(f"invalid host URI (expected host:<id>/relative): {raw!r}")
    host_id = match.group(1).lower()
    raw_relative = match.group(2)
    if raw_relative is None or raw_relative.strip() == "":
        relative = "."
    else:
        relative = PurePosixPath(raw_relative.replace("\\", "/")).as_posix()
    if relative != "." and (
        relative.startswith("../") or "/../" in f"/{relative}/"
    ):
        raise ValueError(f"invalid host relative path: {raw!r}")
    return host_id, relative


def _posix_relative_to(root: Path, target: Path) -> str:
    return target.resolve().relative_to(root.resolve()).as_posix()


def _matches_deny_glob(relative_posix: str, pattern: str) -> bool:
    rel = PurePosixPath(relative_posix)
    pat = pattern.replace("\\", "/")
    if rel.match(pat):
        return True
    # Also check basename-only patterns and infix segment rules.
    if fnmatch(relative_posix, pat):
        return True
    parts = PurePosixPath(relative_posix).parts
    if pat == "**/.env" and ".env" in parts:
        return True
    if pat == "**/.env.*":
        return any(part.startswith(".env.") for part in parts)
    return False


def is_system_denied_path(resolved: Path) -> bool:
    posix = resolved.resolve().as_posix().replace("\\", "/")
    for prefix in _WINDOWS_SYSTEM_PREFIXES:
        if posix == prefix or posix.startswith(prefix + "/"):
            return True
    return False


def is_path_denied(
    resolved: Path,
    *,
    host_root: Path,
    config: HostScopeConfig,
) -> bool:
    """Return True if *resolved* is under *host_root* but blocked by deny rules."""
    root = host_root.resolve()
    target = resolved.resolve()
    if not _is_within(target, root):
        return True
    relative = _posix_relative_to(root, target)
    for pattern in config.deny_globs:
        if _matches_deny_glob(relative, pattern):
            return True
    if config.system_deny and is_system_denied_path(target):
        return True
    return False


def assert_path_not_denied(
    resolved: Path,
    *,
    host_root: Path,
    config: HostScopeConfig,
    raw: str,
) -> None:
    if is_path_denied(resolved, host_root=host_root, config=config):
        raise HostPathDeniedError(
            f"path denied by host scope policy: {raw!r}",
            path=raw,
            reason="denylist",
        )


@dataclass(frozen=True, slots=True)
class ResolvedHostPath:
    """Result of resolving a ``host:<id>/relative`` URI."""

    host_id: str
    host_root: Path
    relative: str
    absolute: Path


def resolve_host_path(
    raw: str,
    *,
    config: HostScopeConfig,
    write: bool = False,
    must_exist: bool = False,
) -> ResolvedHostPath:
    """Resolve ``host:<id>/relative`` under registered host roots (T-1003)."""
    host_id, relative = parse_host_uri(raw)
    entry = config.host_by_id(host_id)
    if entry is None:
        raise HostRootNotFoundError(
            f"unknown host root id: {host_id!r}",
            host_id=host_id,
            path=raw,
        )

    if write:
        if not entry.write:
            raise HostScopePermissionError(
                f"host root {host_id!r} is not writable",
                path=raw,
                host_id=host_id,
            )
    elif not entry.read:
        raise HostScopePermissionError(
            f"host root {host_id!r} is not readable",
            path=raw,
            host_id=host_id,
        )

    root = entry.path.resolve()
    parts = PurePosixPath(relative).parts
    target = root.joinpath(*parts).resolve()

    if not _is_within(target, root):
        raise PathOutOfBoundsError(
            f"path escapes host root {host_id!r}: {raw!r}",
            path=raw,
            boundary=f"host:{host_id}",
        )

    assert_path_not_denied(target, host_root=root, config=config, raw=raw)

    if must_exist and not target.exists():
        raise FileNotFoundError(f"path does not exist: {raw}")

    return ResolvedHostPath(
        host_id=host_id,
        host_root=root,
        relative=relative,
        absolute=target,
    )


def add_host_root(
    paths: AgentPaths,
    config: HostScopeConfig,
    *,
    host_id: str,
    directory: Path | str,
    label: str,
    read: bool = True,
    write: bool = False,
) -> HostRoot:
    """Validate and append a host root (caller saves config)."""
    if not _HOST_ID_RE.fullmatch(host_id):
        raise HostScopeConfigError(
            f"invalid host id {host_id!r}; use [a-z][a-z0-9_-]*"
        )
    if config.host_by_id(host_id) is not None:
        raise HostScopeConfigError(f"host root id already exists: {host_id!r}")
    resolved = validate_host_root_path(directory, agent_root=paths.agent_root)
    if write and not read:
        raise HostScopeConfigError("write requires read=true")
    entry = HostRoot(
        id=host_id,
        path=resolved,
        label=label.strip(),
        added_at=utc_now_iso(),
        read=read,
        write=write,
    )
    config.host_roots.append(entry)
    validate_host_scope(paths, config)
    return entry


def remove_host_root(config: HostScopeConfig, host_id: str) -> HostRoot:
    """Remove a host root by id; returns removed entry."""
    for index, entry in enumerate(config.host_roots):
        if entry.id == host_id:
            removed = config.host_roots.pop(index)
            return removed
    raise HostScopeConfigError(f"host root id not found: {host_id!r}")


def set_host_root_write(
    paths: AgentPaths,
    config: HostScopeConfig,
    host_id: str,
    *,
    write: bool,
) -> HostRoot:
    """Toggle write permission on an existing host root."""
    entry = config.host_by_id(host_id)
    if entry is None:
        raise HostScopeConfigError(f"host root id not found: {host_id!r}")
    if write and not entry.read:
        raise HostScopeConfigError(f"host root {host_id!r} must be readable before write=true")
    updated = HostRoot(
        id=entry.id,
        path=entry.path,
        label=entry.label,
        added_at=entry.added_at,
        read=True if write else entry.read,
        write=write,
    )
    for index, existing in enumerate(config.host_roots):
        if existing.id == host_id:
            config.host_roots[index] = updated
            break
    validate_host_scope(paths, config)
    return updated


def set_host_root_path(
    paths: AgentPaths,
    config: HostScopeConfig,
    host_id: str,
    *,
    directory: Path | str,
) -> HostRoot:
    """Point an existing host root at a different directory."""
    entry = config.host_by_id(host_id)
    if entry is None:
        raise HostScopeConfigError(f"host root id not found: {host_id!r}")
    resolved = validate_host_root_path(directory, agent_root=paths.agent_root)
    updated = HostRoot(
        id=entry.id,
        path=resolved,
        label=entry.label,
        added_at=entry.added_at,
        read=entry.read,
        write=entry.write,
    )
    for index, existing in enumerate(config.host_roots):
        if existing.id == host_id:
            config.host_roots[index] = updated
            break
    validate_host_scope(paths, config)
    return updated


def format_host_root_permissions(entry: HostRoot) -> str:
    if entry.write:
        return "读写"
    if entry.read:
        return "只读"
    return "无"


def _demo() -> None:
    paths = AgentPaths.discover()
    print(f"agent_root: {paths.agent_root}")
    print(f"host_scope: {host_scope_file(paths)}")
    print()

    missing = load_host_scope(paths)
    assert missing.host_roots == []
    print("[PASS] T-1002: missing host_scope.json -> empty host_roots")

    if host_root_overlaps_agent(paths.agent_root, paths.agent_root):
        print("[PASS] T-1002: agent root overlaps itself")
    else:
        print("[FAIL] T-1002: expected agent root overlap")
        raise SystemExit(1)

    if host_root_overlaps_agent(paths.workspace, paths.agent_root):
        print("[PASS] T-1002: workspace under agent root rejected for host registration")
    else:
        print("[FAIL] T-1002: workspace should overlap agent root")
        raise SystemExit(1)

    with tempfile.TemporaryDirectory(prefix="host-scope-t1002-") as tmp:
        ext_dir = Path(tmp)
        try:
            validate_host_root_path(ext_dir, agent_root=paths.agent_root)
            print("[PASS] T-1002: external directory valid as host root candidate")
        except HostScopeConfigError:
            print("[FAIL] T-1002: external directory should be valid")
            raise SystemExit(1)

        try:
            validate_host_root_path(paths.workspace, agent_root=paths.agent_root)
            print("[FAIL] T-1002: workspace should be rejected as host root")
            raise SystemExit(1)
        except HostScopeConfigError:
            print("[PASS] T-1002: reject host root inside agent tree")

        config = empty_host_scope()
        add_host_root(
            paths,
            config,
            host_id="demoext",
            directory=ext_dir,
            label="Demo External",
            read=True,
            write=False,
        )
        secret = ext_dir / ".ssh" / "id_rsa"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("fake-key", encoding="utf-8")
        if is_path_denied(secret, host_root=ext_dir, config=config):
            print("[PASS] T-1002: deny_glob blocks .ssh/id_rsa under host root")
        else:
            print("[FAIL] T-1002: expected .ssh/id_rsa denied")
            raise SystemExit(1)

        safe = ext_dir / "notes.txt"
        safe.write_text("hello", encoding="utf-8")
        if not is_path_denied(safe, host_root=ext_dir, config=config):
            print("[PASS] T-1002: ordinary file not denied")
        else:
            print("[FAIL] T-1002: notes.txt should be allowed")
            raise SystemExit(1)

        host_id, rel = parse_host_uri("host:demoext/notes.txt")
        assert host_id == "demoext" and rel == "notes.txt"
        print("[PASS] T-1002: parse_host_uri host:demoext/notes.txt")

        root_id, root_rel = parse_host_uri("host:downloads")
        assert root_id == "downloads" and root_rel == "."
        print("[PASS] T-1002: parse_host_uri host:downloads (root)")

        try:
            parse_host_uri("C:/Windows/System32")
            print("[FAIL] T-1002: bare absolute path should be rejected")
            raise SystemExit(1)
        except ValueError:
            print("[PASS] T-1002: reject bare absolute path in parse_host_uri")

        demo_paths = AgentPaths.from_root(paths.agent_root)
        demo_file = host_scope_file(demo_paths)
        backup: str | None = None
        if demo_file.is_file():
            backup = demo_file.read_text(encoding="utf-8")
        try:
            save_host_scope(demo_paths, config)
            reloaded = load_host_scope(demo_paths)
            assert len(reloaded.host_roots) == 1
            assert reloaded.host_roots[0].id == "demoext"
            print("[PASS] T-1002: save_host_scope + load roundtrip")
        finally:
            if backup is None:
                demo_file.unlink(missing_ok=True)
            else:
                demo_file.write_text(backup, encoding="utf-8")

    if is_system_denied_path(Path("C:/Windows/System32/drivers/etc/hosts")):
        print("[PASS] T-1002: system_deny blocks Windows system path")
    else:
        print("[SKIP] T-1002: system_deny Windows check (non-Windows env)")


if __name__ == "__main__":
    _demo()
