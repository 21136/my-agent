"""Sandbox worktree helpers for self-evolution sessions (SANDBOX-EVOLUTION)."""

from __future__ import annotations

import os
from pathlib import Path

from paths import AgentPaths, PathError

SANDBOX_WRITE_SCOPE_DEFAULT = ".worktrees/sandbox"
_SEED_PREFIX = "[实验进化会话]"


def is_sandbox_agent_root(paths: AgentPaths) -> bool:
    """True when this process is already the sandbox worktree (8766), not the stable root."""
    root = paths.agent_root.resolve()
    raw = (os.environ.get("MY_AGENT_SANDBOX_ROOT") or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = paths.agent_root / candidate
        try:
            if candidate.resolve() == root:
                return True
        except OSError:
            pass
    parts = root.parts
    return len(parts) >= 2 and parts[-2] == ".worktrees" and parts[-1] == "sandbox"


def resolve_sandbox_root(paths: AgentPaths) -> Path:
    """Absolute path to the sandbox git worktree (may not exist)."""
    if is_sandbox_agent_root(paths):
        return paths.agent_root.resolve()
    raw = (os.environ.get("MY_AGENT_SANDBOX_ROOT") or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = paths.agent_root / candidate
        return candidate.resolve()
    return (paths.agent_root / ".worktrees" / "sandbox").resolve()


def _evolve_markers_present(root: Path) -> bool:
    return any(
        (root / "evolve" / name).is_file()
        for name in ("_index.core.toml", "_index.toml")
    )


def sandbox_worktree_ready(paths: AgentPaths) -> bool:
    """True when sandbox exists and looks like an agent root."""
    root = resolve_sandbox_root(paths)
    return root.is_dir() and _evolve_markers_present(root)


def sandbox_write_scope_relative(paths: AgentPaths) -> str:
    """POSIX-style path under agent root for session write_scope_root.

    Sandbox sidecar already lives in the experiment tree, so the write fence is
    the whole agent root (empty scope). Stable sidecar fences writes to the nested worktree.
    """
    if is_sandbox_agent_root(paths):
        return ""
    root = resolve_sandbox_root(paths)
    if not root.is_dir():
        raise PathError(f"sandbox 目录不存在：{root}")
    try:
        rel = paths.to_agent_relative(root)
    except (ValueError, PathError) as exc:
        raise PathError(f"sandbox 不在当前 Agent 根下：{root}") from exc
    return normalize_write_scope_root(rel)


def coerce_session_write_path(paths: AgentPaths, raw: str, scope_root: str) -> str:
    """Map a repo-relative write onto the session sandbox tree.

    Experiment sessions are often told to edit ``desktop/…`` or ``agent-core/…``.
    Those paths are the stable checkout when the sidecar is not already inside
    the worktree. Put them under the write scope instead of rejecting the turn.
    """
    scope = scope_root.strip().replace("\\", "/").strip("/")
    text = raw.strip().replace("\\", "/")
    if not scope or not text or text.lower().startswith("host:"):
        return raw
    if any(part == ".." for part in text.split("/")):
        return raw
    if text == scope or text.startswith(scope + "/"):
        return text
    candidate = Path(raw.strip())
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve()
            agent = paths.agent_root.resolve()
            scope_dir = (agent / scope).resolve()
            if _path_is_within(resolved, scope_dir):
                return resolved.relative_to(agent).as_posix()
            if _path_is_within(resolved, agent):
                rel = resolved.relative_to(agent).as_posix()
                if rel == scope or rel.startswith(scope + "/"):
                    return rel
                return f"{scope}/{rel}"
        except (OSError, ValueError):
            return raw
        return raw
    return f"{scope}/{text.lstrip('/')}"


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def normalize_write_scope_root(raw: str) -> str:
    text = raw.strip().replace("\\", "/").strip("/")
    if not text or ".." in text.split("/"):
        raise PathError(f"非法写入范围：{raw!r}")
    return text


def write_scope_label(scope_root: str) -> str:
    scope = normalize_write_scope_root(scope_root) if scope_root.strip() else ""
    if not scope:
        return ""
    return f"写入仅限 {scope}"


def sandbox_session_seed_content(scope_root: str) -> str:
    scope = scope_root.strip()
    if not scope:
        return "\n".join(
            [
                _SEED_PREFIX,
                "本会话跑在实验树（sandbox sidecar）。这就是自我修改：直接改本树的 desktop/、agent-core/、docs/ 和 evolve 工具。",
                "不要让用户去 Cursor / Codex 代写；用 write_text、patch_file、write_evolve 在本树落地。",
                "稳定根目录只读。UI 验收看 5174。改完后请用户在双车道点「合入稳定」。",
            ]
        )
    scope = normalize_write_scope_root(scope_root)
    return "\n".join(
        [
            _SEED_PREFIX,
            f"本会话 write_scope_root={scope}。",
            "所有写文件类工具（write_text、patch_file、write_evolve、generate_image 落盘等）"
            f"只能落在该目录下；稳定树与其它路径只读参考。",
            "这就是自我修改：直接改 sandbox 里的 desktop/ 与 agent-core/，不要让用户去 Cursor 代写。",
            "壳 UI 改动用侧栏「双车道」看 5174。改完后请用户点「合入稳定」。",
        ]
    )
