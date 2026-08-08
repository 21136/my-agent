"""Terminal startup scope resolution (TERMINAL-MODE §4.2–4.3 · T-5708)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from host_scope import (
    HostScopeConfig,
    HostScopeConfigError,
    add_host_root,
    is_system_denied_path,
    load_host_scope,
    save_host_scope,
    validate_host_scope,
)
from paths import AgentPaths, _is_within
from session import (
    SessionMeta,
    TerminalScopeKind,
    normalize_terminal_path_field,
)

R3_PROMPT_HEADER = "当前目录不在 my-agent 内，也未登记托管区："


class TerminalScopeError(Exception):
    """Invalid terminal cwd or scope resolution."""


@dataclass(frozen=True, slots=True)
class TerminalScopeFields:
    """Scope fields for ``create_terminal_session`` (TERMINAL-MODE §4.1)."""

    terminal_scope_kind: TerminalScopeKind
    terminal_cwd: str = ""
    terminal_foreign_root: str = ""
    terminal_host_id: str = ""


@dataclass(frozen=True, slots=True)
class TerminalStartupDenied:
    """R4 or user cancel — caller should exit."""

    message: str
    cwd: str


@dataclass(frozen=True, slots=True)
class TerminalStartupNeedsPrompt:
    """R3 — caller must prompt user (or supply ``input_fn``)."""

    prompt: str
    cwd: Path


TerminalStartupOutcome = (
    TerminalScopeFields | TerminalStartupDenied | TerminalStartupNeedsPrompt
)


def format_r3_prompt(cwd: Path) -> str:
    """Frozen R3 prompt (TERMINAL-MODE §4.3)."""
    absolute = cwd.resolve().as_posix()
    return (
        f"{R3_PROMPT_HEADER}\n"
        f"  {absolute}\n"
        "\n"
        "  [1] 仅本次使用此目录（不写入托管区配置）\n"
        "  [2] 登记为托管区（读写）后进入\n"
        "  [3] 取消\n"
        "\n"
        "请选择 1/2/3："
    )


def resolve_terminal_cwd_candidate(
    path_arg: str | None,
    *,
    shell_cwd: Path | None = None,
) -> Path:
    """Resolve startup cwd: no arg = shell cwd; relative paths use shell cwd (TM-21)."""
    base = (shell_cwd or Path.cwd()).expanduser().resolve()
    if path_arg is None or not str(path_arg).strip():
        candidate = base
    else:
        raw = path_arg.strip()
        path = Path(raw)
        candidate = path if path.is_absolute() else base / path
    resolved = candidate.expanduser().resolve()
    if not resolved.is_dir():
        raise TerminalScopeError(
            f"terminal cwd is not a directory: {resolved.as_posix()}"
        )
    return resolved


def is_terminal_startup_denied(cwd: Path, config: HostScopeConfig) -> bool:
    """R4: cwd is a sensitive location (``.ssh`` · ``.env`` · credentials · system paths)."""
    target = cwd.resolve()
    if config.system_deny and is_system_denied_path(target):
        return True

    parts_lower = [part.casefold() for part in target.parts]
    if ".ssh" in parts_lower or ".gnupg" in parts_lower:
        return True
    if "node_modules" in parts_lower:
        return True

    basename = target.name.casefold()
    if basename == ".env" or basename.startswith(".env."):
        return True
    if basename in {"id_rsa", "id_rsa.pub"}:
        return True
    if any("credentials" in part for part in parts_lower):
        return True

    # AppData config areas (not Temp): .../AppData/Roaming|Local/<sensitive>
    for index, part in enumerate(parts_lower):
        if part != "appdata" or index + 1 >= len(parts_lower):
            continue
        child = parts_lower[index + 1]
        if child == "roaming":
            return True
        if child == "local" and index + 2 < len(parts_lower):
            grandchild = parts_lower[index + 2]
            if grandchild not in {"temp", "tmp"}:
                return True
    return False


def _agent_relative_cwd(agent_root: Path, cwd: Path) -> str:
    relative = cwd.resolve().relative_to(agent_root.resolve()).as_posix()
    return normalize_terminal_path_field(relative) or "."


def _host_relative_cwd(host_root: Path, cwd: Path) -> str:
    relative = cwd.resolve().relative_to(host_root.resolve()).as_posix()
    return normalize_terminal_path_field(relative) or "."


def _find_readable_host_root(cwd: Path, config: HostScopeConfig):
    target = cwd.resolve()
    best = None
    best_depth = -1
    for entry in config.host_roots:
        if not entry.read:
            continue
        root = entry.path.resolve()
        if not _is_within(target, root):
            continue
        depth = len(root.parts)
        if depth > best_depth:
            best = entry
            best_depth = depth
    return best


def classify_terminal_startup(
    paths: AgentPaths,
    cwd: Path,
    *,
    config: HostScopeConfig | None = None,
) -> TerminalStartupOutcome:
    """Apply R4 → R1 → R2 → R3 to *cwd* (already resolved directory)."""
    scope_config = config if config is not None else load_host_scope(paths)
    resolved = cwd.resolve()

    if is_terminal_startup_denied(resolved, scope_config):
        return TerminalStartupDenied(
            message=(
                "terminal startup denied: cwd matches deny policy "
                f"({resolved.as_posix()})"
            ),
            cwd=resolved.as_posix(),
        )

    agent_root = paths.agent_root.resolve()
    if _is_within(resolved, agent_root):
        return TerminalScopeFields(
            terminal_scope_kind="agent",
            terminal_cwd=_agent_relative_cwd(agent_root, resolved),
        )

    host = _find_readable_host_root(resolved, scope_config)
    if host is not None:
        return TerminalScopeFields(
            terminal_scope_kind="host",
            terminal_cwd=_host_relative_cwd(host.path, resolved),
            terminal_host_id=host.id,
        )

    return TerminalStartupNeedsPrompt(
        prompt=format_r3_prompt(resolved),
        cwd=resolved,
    )


def apply_r3_choice(
    choice: str,
    cwd: Path,
    paths: AgentPaths,
    *,
    config: HostScopeConfig | None = None,
    host_id: str = "",
    host_label: str = "",
    host_write: bool = True,
) -> TerminalScopeFields | None:
    """Apply R3 selection. Returns ``None`` for choice 3 (cancel)."""
    text = choice.strip()
    resolved = cwd.resolve()

    if text == "3":
        return None
    if text == "1":
        return TerminalScopeFields(
            terminal_scope_kind="foreign",
            terminal_foreign_root=normalize_terminal_path_field(
                resolved.as_posix(), relative=False
            ),
        )
    if text == "2":
        if not host_id.strip():
            raise TerminalScopeError("R3 choice 2 requires host_id")
        label = host_label.strip() or host_id.strip()
        scope_config = config if config is not None else load_host_scope(paths)
        try:
            add_host_root(
                paths,
                scope_config,
                host_id=host_id.strip(),
                directory=resolved,
                label=label,
                read=True,
                write=host_write,
            )
            validate_host_scope(paths, scope_config)
            save_host_scope(paths, scope_config)
        except HostScopeConfigError as exc:
            raise TerminalScopeError(str(exc)) from exc
        outcome = classify_terminal_startup(paths, resolved, config=scope_config)
        if isinstance(outcome, TerminalScopeFields):
            return outcome
        if isinstance(outcome, TerminalStartupDenied):
            raise TerminalScopeError(outcome.message)
        raise TerminalScopeError("host registration did not yield host scope")

    raise TerminalScopeError(f"invalid R3 choice: {choice!r} (expected 1, 2, or 3)")


def resolve_terminal_startup_scope(
    paths: AgentPaths,
    path_arg: str | None = None,
    *,
    shell_cwd: Path | None = None,
    input_fn: Callable[[str], str] | None = None,
    config: HostScopeConfig | None = None,
    r3_host_id: str = "",
    r3_host_label: str = "",
    r3_host_write: bool = True,
) -> TerminalScopeFields | TerminalStartupDenied:
    """Resolve cwd + R1–R4; run R3 prompt when ``input_fn`` is provided."""
    cwd = resolve_terminal_cwd_candidate(path_arg, shell_cwd=shell_cwd)
    outcome = classify_terminal_startup(paths, cwd, config=config)
    if isinstance(outcome, TerminalScopeFields):
        return outcome
    if isinstance(outcome, TerminalStartupDenied):
        return outcome

    if input_fn is None:
        return TerminalStartupDenied(
            message="terminal startup requires R3 choice but no input_fn was provided",
            cwd=cwd.resolve().as_posix(),
        )

    answer = input_fn(outcome.prompt)
    chosen = apply_r3_choice(
        answer,
        cwd,
        paths,
        config=config,
        host_id=r3_host_id,
        host_label=r3_host_label,
        host_write=r3_host_write,
    )
    if chosen is None:
        return TerminalStartupDenied(message="terminal startup cancelled", cwd=cwd.as_posix())
    return chosen


def resolve_terminal_effective_root(
    meta: SessionMeta,
    paths: AgentPaths,
    *,
    config: HostScopeConfig | None = None,
) -> Path:
    """Effective tool root for a terminal session (TERMINAL-MODE §4.2)."""
    if meta.harness != "terminal":
        raise TerminalScopeError("resolve_terminal_effective_root requires harness=terminal")
    kind = meta.terminal_scope_kind
    if kind == "agent":
        base = paths.agent_root.resolve()
        rel = meta.terminal_cwd.strip()
        if not rel or rel == ".":
            return base
        return (base / rel).resolve()
    if kind == "host":
        scope_config = config if config is not None else load_host_scope(paths)
        entry = scope_config.host_by_id(meta.terminal_host_id)
        if entry is None:
            raise TerminalScopeError(
                f"unknown terminal_host_id: {meta.terminal_host_id!r}"
            )
        root = entry.path.resolve()
        rel = meta.terminal_cwd.strip()
        if not rel or rel == ".":
            return root
        return (root / rel).resolve()
    if kind == "foreign":
        if not meta.terminal_foreign_root.strip():
            raise TerminalScopeError("terminal_foreign_root is required for foreign scope")
        return Path(meta.terminal_foreign_root).expanduser().resolve()
    raise TerminalScopeError(f"unsupported terminal_scope_kind: {kind!r}")


def scope_fields_from_meta(meta: SessionMeta) -> TerminalScopeFields:
    """Rebuild scope fields from a terminal session meta."""
    if meta.harness != "terminal" or not meta.terminal_scope_kind:
        raise TerminalScopeError("scope_fields_from_meta requires terminal harness meta")
    return TerminalScopeFields(
        terminal_scope_kind=meta.terminal_scope_kind,
        terminal_cwd=meta.terminal_cwd,
        terminal_foreign_root=meta.terminal_foreign_root,
        terminal_host_id=meta.terminal_host_id,
    )


def scope_fields_to_session_kwargs(fields: TerminalScopeFields) -> dict[str, str]:
    """Kwargs fragment for ``create_terminal_session``."""
    return {
        "terminal_scope_kind": fields.terminal_scope_kind,
        "terminal_cwd": fields.terminal_cwd,
        "terminal_foreign_root": fields.terminal_foreign_root,
        "terminal_host_id": fields.terminal_host_id,
    }


def _resolve_tool_path_for_terminal(
    raw: str,
    meta: SessionMeta,
    agent_paths: AgentPaths,
) -> Path | None:
    text = raw.strip().replace("\\", "/")
    if not text:
        return None
    if text.lower().startswith("host:"):
        from host_scope import load_host_scope, resolve_host_path

        config = load_host_scope(agent_paths)
        try:
            resolved = resolve_host_path(text, config=config, write=False)
            return resolved.absolute.resolve()
        except Exception:
            return None
    try:
        if meta.terminal_scope_kind == "foreign":
            candidate = Path(text)
            if candidate.is_absolute():
                return candidate.expanduser().resolve()
        return agent_paths.resolve_under_agent(text).resolve()
    except Exception:
        try:
            return Path(text).expanduser().resolve()
        except Exception:
            return None


def path_is_under_terminal_effective_root(
    raw: str,
    meta: SessionMeta,
    agent_paths: AgentPaths,
) -> bool:
    """True when *raw* resolves inside the terminal effective root (§4.2)."""
    try:
        root = resolve_terminal_effective_root(meta, agent_paths)
    except TerminalScopeError:
        return False
    resolved = _resolve_tool_path_for_terminal(raw, meta, agent_paths)
    if resolved is None:
        return False
    return _is_within(resolved, root.resolve())


def terminal_codebase_search_root(meta: SessionMeta, agent_paths: AgentPaths) -> str:
    """Agent-relative posix path for codebase_search index scope."""
    root = resolve_terminal_effective_root(meta, agent_paths)
    agent = agent_paths.agent_root.resolve()
    try:
        return root.resolve().relative_to(agent).as_posix()
    except ValueError:
        return root.resolve().as_posix()


def terminal_write_requires_confirm(
    *,
    tool: str,
    path: str,
    meta: SessionMeta,
    agent_paths: AgentPaths,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Layered confirm for terminal harness writes (§5.3 · TM-5/TM-7)."""
    from write_policy import is_sensitive_write_path

    if dry_run:
        return False, "skip:dry_run"
    raw = (path or "").strip()
    if not raw:
        return True, "confirm:empty_path"
    norm = raw.replace("\\", "/").lstrip("/")
    if norm.lower().startswith("evolve/"):
        return True, "confirm:evolve"
    if not path_is_under_terminal_effective_root(raw, meta, agent_paths):
        return True, "confirm:outside_terminal_root"
    if is_sensitive_write_path(raw):
        return True, "confirm:sensitive"
    return False, f"skip:terminal_wild:{tool}"


def terminal_run_command_requires_confirm(
    *,
    working_dir: str,
    meta: SessionMeta,
    agent_paths: AgentPaths,
) -> tuple[bool, str]:
    """Terminal wild run_command inside effective root skips confirm."""
    wd = (working_dir or "").strip()
    if not wd:
        return False, "skip:terminal_wild_default_cwd"
    if path_is_under_terminal_effective_root(wd, meta, agent_paths):
        return False, "skip:terminal_wild"
    return True, "confirm:outside_terminal_root"


def terminal_host_write_block_reason(
    meta: SessionMeta,
    agent_paths: AgentPaths,
    *,
    tool_name: str,
    evolved_name: str,
) -> str | None:
    """TM-20: read-only host blocks write tools."""
    if meta.harness != "terminal" or meta.terminal_scope_kind != "host":
        return None
    write_tools = {"write_text", "append_text", "patch_file", "copy_move"}
    if tool_name != "run_evolved" or evolved_name not in write_tools:
        return None
    config = load_host_scope(agent_paths)
    entry = config.host_by_id(meta.terminal_host_id)
    if entry is None:
        return f"unknown terminal host id: {meta.terminal_host_id!r}"
    if not entry.write:
        return (
            f"host root {entry.id!r} is read-only; "
            "write_text/patch_file are not allowed (TM-20)"
        )
    return None


def _demo() -> None:
    paths = AgentPaths.discover()
    cwd = Path.cwd().resolve()
    outcome = classify_terminal_startup(paths, cwd)
    print(f"cwd={cwd.as_posix()}")
    print(f"outcome={outcome!r}")
    if isinstance(outcome, TerminalScopeFields):
        meta = SessionMeta(
            harness="terminal",
            terminal_scope_kind=outcome.terminal_scope_kind,
            terminal_cwd=outcome.terminal_cwd,
            terminal_foreign_root=outcome.terminal_foreign_root,
            terminal_host_id=outcome.terminal_host_id,
        )
        print(f"effective_root={resolve_terminal_effective_root(meta, paths)}")


if __name__ == "__main__":
    _demo()
