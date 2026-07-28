"""Agent root and workspace path resolution (TASKS T-102, TOOLS.md §2 / §7.1).

All tool paths are resolved relative to a discovered agent root. Writes that are
``workspace_only`` must stay inside ``workspace/``. Traversal via ``..`` or
symlinks that escape the boundary is rejected with :class:`PathOutOfBoundsError`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tools.schema import ToolErrorCode

if TYPE_CHECKING:
    from host_scope import HostScopeConfig

_AGENT_MARKERS = (
    Path("evolve") / "_index.core.toml",
    Path("evolve") / "_index.toml",
)
_STATE_REL = Path("data") / "state.json"

_STATE_CORRUPTION_NOTICE = (
    "全局状态文件损坏，已按空索引降级（state.json）。"
    "壳/项目会话映射可能已丢失，下次切换会重建。"
)


def format_state_corruption_notice() -> str:
    """User-facing text when ``data/state.json`` is structurally unreadable (T-1823-05)."""
    return _STATE_CORRUPTION_NOTICE


def note_state_corruption(paths: AgentPaths) -> None:
    """Record a once-per-paths notice for corrupt state.json (idempotent)."""
    text = format_state_corruption_notice()
    if text not in paths.corruption_notices:
        paths.corruption_notices.append(text)


def read_agent_state_payload(
    paths: AgentPaths,
    *,
    note_corruption: bool = True,
) -> dict[str, Any]:
    """Load ``data/state.json`` as a dict; corrupt/missing → ``{}``.

    Missing file is normal (no notice). Unreadable JSON or non-object roots
    degrade to ``{}`` and optionally append to ``paths.corruption_notices``.
    """
    state_path = paths.data / "state.json"
    if not state_path.is_file():
        return {}
    try:
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        if note_corruption:
            note_state_corruption(paths)
        return {}
    if not isinstance(loaded, dict):
        if note_corruption:
            note_state_corruption(paths)
        return {}
    return loaded


def write_agent_state_payload(paths: AgentPaths, payload: dict[str, Any]) -> None:
    state_path = paths.data / "state.json"
    paths.data.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class PathError(Exception):
    """Base class for path resolution errors."""


class PathOutOfBoundsError(PathError):
    """Resolved path escapes the allowed directory boundary."""

    code = ToolErrorCode.PATH_OUT_OF_BOUNDS

    def __init__(self, message: str, *, path: str, boundary: str) -> None:
        super().__init__(message)
        self.path = path
        self.boundary = boundary


@dataclass(frozen=True, slots=True)
class AgentPaths:
    """Resolved agent root and standard subdirectories."""

    agent_root: Path
    workspace: Path
    data: Path
    evolve: Path
    # Ephemeral; mutated in place when state.json is structurally bad (T-1823-05 / IT-56).
    corruption_notices: list[str] = field(default_factory=list, compare=False)

    @classmethod
    def discover(cls, start: Path | str | None = None) -> AgentPaths:
        """Locate agent root and return path helpers."""
        return cls.from_root(find_agent_root(start=start))

    @classmethod
    def from_root(cls, agent_root: Path | str) -> AgentPaths:
        root = Path(agent_root).resolve()
        if not _is_agent_root(root):
            marker_hint = " or ".join(str(marker) for marker in _AGENT_MARKERS)
            raise FileNotFoundError(f"not an agent root (missing {marker_hint}): {root}")
        return cls(
            agent_root=root,
            workspace=root / "workspace",
            data=root / "data",
            evolve=root / "evolve",
        )

    def resolve_under_agent(self, raw: str, *, must_exist: bool = False) -> Path:
        """Resolve *raw* under agent root (read_file / list_dir / grep)."""
        return self._resolve(raw, base=self.agent_root, must_exist=must_exist)

    def resolve_under_workspace(self, raw: str, *, must_exist: bool = False) -> Path:
        """Resolve *raw* under workspace/ (write_text, workspace_only evolved)."""
        return self._resolve(raw, base=self.workspace, must_exist=must_exist)

    def resolve_under_host(
        self,
        raw: str,
        *,
        config: HostScopeConfig,
        write: bool = False,
        must_exist: bool = False,
    ) -> Path:
        """Resolve ``host:<id>/relative`` (HOST-SCOPE S1, T-1003)."""
        from host_scope import resolve_host_path

        return resolve_host_path(
            raw,
            config=config,
            write=write,
            must_exist=must_exist,
        ).absolute

    def is_under_agent(self, path: Path | str) -> bool:
        return _is_within(Path(path).resolve(), self.agent_root.resolve())

    def is_under_workspace(self, path: Path | str) -> bool:
        return _is_within(Path(path).resolve(), self.workspace.resolve())

    def to_agent_relative(self, path: Path | str) -> str:
        """POSIX-style path relative to agent root (for tool payloads / logs)."""
        resolved = Path(path).resolve()
        _assert_within(resolved, self.agent_root, raw=str(path), label="agent root")
        return resolved.relative_to(self.agent_root.resolve()).as_posix()

    def to_workspace_relative(self, path: Path | str) -> str:
        resolved = Path(path).resolve()
        _assert_within(resolved, self.workspace, raw=str(path), label="workspace")
        return resolved.relative_to(self.workspace.resolve()).as_posix()

    def _resolve(self, raw: str, *, base: Path, must_exist: bool) -> Path:
        if not isinstance(raw, str):
            raise TypeError("path must be a string")
        text = raw.strip()
        if not text:
            raise ValueError("path must be non-empty")
        if "\0" in text:
            raise ValueError("path contains null byte")

        base_resolved = base.resolve()
        candidate = Path(text)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (base_resolved / candidate).resolve()

        label = "agent root" if base_resolved == self.agent_root.resolve() else "workspace"
        _assert_within(resolved, base_resolved, raw=text, label=label)

        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"path does not exist: {text}")
        return resolved


def find_agent_root(*, start: Path | str | None = None) -> Path:
    """Walk upward to locate the directory containing evolve index marker.

    Accepts ``evolve/_index.core.toml`` (Phase 8) or legacy ``evolve/_index.toml``.

    Resolution order:
    1. ``MY_AGENT_ROOT`` environment variable (if valid)
    2. Walk up from *start* (default: cwd)
    3. Walk up from this file's parent (``agent-core/`` → repo root)
    """
    env_root = os.environ.get("MY_AGENT_ROOT")
    if env_root:
        root = Path(env_root).expanduser()
        if _is_agent_root(root):
            return root.resolve()
        marker_hint = " or ".join(str(marker) for marker in _AGENT_MARKERS)
        raise FileNotFoundError(
            f"MY_AGENT_ROOT is set but missing evolve index ({marker_hint}): {root}"
        )

    origins: list[Path] = []
    if start is not None:
        origins.append(Path(start))
    else:
        origins.append(Path.cwd())

    module_guess = Path(__file__).resolve().parent.parent
    if module_guess not in origins:
        origins.append(module_guess)

    for origin in origins:
        found = _walk_up(origin)
        if found is not None:
            return found

    marker_hint = " or ".join(str(marker) for marker in _AGENT_MARKERS)
    raise FileNotFoundError(
        f"could not locate agent root (missing {marker_hint}); "
        "set MY_AGENT_ROOT or run from inside the repo"
    )


def _walk_up(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        if _is_agent_root(directory):
            state_root = _agent_root_from_state(directory)
            if state_root is not None:
                return state_root
            return directory.resolve()
    return None


def _is_agent_root(directory: Path) -> bool:
    try:
        return any((directory / marker).is_file() for marker in _AGENT_MARKERS)
    except OSError:
        return False


def _agent_root_from_state(directory: Path) -> Path | None:
    state_path = directory / _STATE_REL
    if not state_path.is_file():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = payload.get("agent_root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    root = Path(raw).expanduser().resolve()
    if _is_agent_root(root):
        return root
    return None


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _assert_within(resolved: Path, base: Path, *, raw: str, label: str) -> None:
    base_resolved = base.resolve()
    if not _is_within(resolved, base_resolved):
        raise PathOutOfBoundsError(
            f"path escapes {label}: {raw!r}",
            path=raw,
            boundary=label,
        )


def _demo() -> None:
    paths = AgentPaths.discover()

    checks: list[tuple[str, Literal["agent", "workspace"], str, bool]] = [
        ("docs/PROJECT.md", "agent", "ok under agent", True),
        ("workspace", "agent", "workspace dir under agent", True),
        ("../outside-agent", "agent", "reject escape via ..", False),
        ("..\\..\\Windows", "agent", "reject windows escape", False),
        ("out.txt", "workspace", "ok under workspace", True),
        ("../evolve/_index.toml", "workspace", "reject escape to evolve", False),
    ]

    print(f"agent_root: {paths.agent_root}")
    print(f"workspace:  {paths.workspace}")
    print()

    for raw, scope, label, should_pass in checks:
        try:
            if scope == "agent":
                resolved = paths.resolve_under_agent(raw)
            else:
                resolved = paths.resolve_under_workspace(raw)
            if should_pass:
                rel = (
                    paths.to_agent_relative(resolved)
                    if scope == "agent"
                    else paths.to_workspace_relative(resolved)
                )
                print(f"[PASS] {label}: {raw!r} -> {rel}")
            else:
                print(f"[FAIL] {label}: expected rejection, got {resolved}")
                raise SystemExit(1)
        except PathOutOfBoundsError:
            if should_pass:
                print(f"[FAIL] {label}: unexpected PathOutOfBoundsError for {raw!r}")
                raise SystemExit(1)
            print(f"[PASS] {label}: rejected {raw!r}")

    project = paths.resolve_under_agent("docs/PROJECT.md", must_exist=True)
    assert project.is_file()
    print(f"[PASS] must_exist: {project.name}")

    _demo_t1003(paths)


def _demo_t1003(paths: AgentPaths) -> None:
    from host_scope import (
        HostPathDeniedError,
        HostRootNotFoundError,
        HostScopePermissionError,
        add_host_root,
        empty_host_scope,
        resolve_host_path,
    )

    print()
    print("--- T-1003 host resolve ---")

    with tempfile.TemporaryDirectory(prefix="paths-t1003-") as tmp:
        host_dir = Path(tmp)
        notes = host_dir / "notes.txt"
        notes.write_text("hello host", encoding="utf-8")
        secret_dir = host_dir / ".ssh"
        secret_dir.mkdir()
        (secret_dir / "id_rsa").write_text("fake", encoding="utf-8")

        config = empty_host_scope()
        add_host_root(
            paths,
            config,
            host_id="downloads",
            directory=host_dir,
            label="Test Downloads",
            read=True,
            write=False,
        )

        resolved = paths.resolve_under_host(
            "host:downloads/notes.txt",
            config=config,
            must_exist=True,
        )
        assert resolved == notes.resolve()
        print("[PASS] T-1003: host:downloads/notes.txt resolves")

        try:
            paths.resolve_under_host("host:unknown/foo", config=config)
            print("[FAIL] T-1003: unknown host id should be rejected")
            raise SystemExit(1)
        except HostRootNotFoundError:
            print("[PASS] T-1003: reject host:unknown/foo")

        escape_attempts = (
            "host:downloads/../../outside",
            "host:downloads/foo/../../../outside",
        )
        for raw in escape_attempts:
            try:
                parse_host_uri = __import__("host_scope", fromlist=["parse_host_uri"]).parse_host_uri
                parse_host_uri(raw)
                paths.resolve_under_host(raw, config=config)
                print(f"[FAIL] T-1003: expected rejection for {raw!r}")
                raise SystemExit(1)
            except (ValueError, PathOutOfBoundsError):
                print(f"[PASS] T-1003: reject escape {raw!r}")

        try:
            paths.resolve_under_host("host:downloads/.ssh/id_rsa", config=config)
            print("[FAIL] T-1003: .ssh/id_rsa should be path_denied")
            raise SystemExit(1)
        except HostPathDeniedError as exc:
            assert exc.code == ToolErrorCode.PATH_DENIED
            print("[PASS] T-1003: host:downloads/.ssh/id_rsa -> path_denied")

        try:
            paths.resolve_under_host(
                "host:downloads/notes.txt",
                config=config,
                write=True,
            )
            print("[FAIL] T-1003: write on read-only root should be rejected")
            raise SystemExit(1)
        except HostScopePermissionError:
            print("[PASS] T-1003: write rejected when host root write=false")

        config_write = empty_host_scope()
        add_host_root(
            paths,
            config_write,
            host_id="docs",
            directory=host_dir,
            label="Writable test",
            read=True,
            write=True,
        )
        out = paths.resolve_under_host(
            "host:docs/new.txt",
            config=config_write,
            write=True,
        )
        assert out.parent == host_dir.resolve()
        print("[PASS] T-1003: write resolve allowed when host root write=true")

        direct = resolve_host_path("host:downloads/notes.txt", config=config, must_exist=True)
        assert direct.relative == "notes.txt"
        print("[PASS] T-1003: ResolvedHostPath.relative")


if __name__ == "__main__":
    _demo()
