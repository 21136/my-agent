"""Sandbox worktree helpers for self-evolution sessions (SANDBOX-EVOLUTION)."""

from __future__ import annotations

import os
from pathlib import Path

from paths import AgentPaths, PathError

SANDBOX_WRITE_SCOPE_DEFAULT = ".worktrees/sandbox"
_SEED_PREFIX = "[实验进化会话]"


def resolve_sandbox_root(paths: AgentPaths) -> Path:
    """Absolute path to the sandbox git worktree (may not exist)."""
    raw = (os.environ.get("MY_AGENT_SANDBOX_ROOT") or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = paths.agent_root / candidate
        return candidate.resolve()
    return (paths.agent_root / ".worktrees" / "sandbox").resolve()


def sandbox_worktree_ready(paths: AgentPaths) -> bool:
    """True when sandbox exists and looks like an agent root."""
    root = resolve_sandbox_root(paths)
    if not root.is_dir():
        return False
    markers = (
        root / "evolve" / "_index.core.toml",
        root / "evolve" / "_index.toml",
    )
    return any(m.is_file() for m in markers)


def sandbox_write_scope_relative(paths: AgentPaths) -> str:
    """POSIX-style path under agent root for session write_scope_root."""
    root = resolve_sandbox_root(paths)
    if not root.is_dir():
        raise PathError(f"sandbox 目录不存在：{root}")
    try:
        rel = paths.to_agent_relative(root)
    except (ValueError, PathError) as exc:
        raise PathError(f"sandbox 不在当前 Agent 根下：{root}") from exc
    return normalize_write_scope_root(rel)


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
    scope = normalize_write_scope_root(scope_root)
    return "\n".join(
        [
            _SEED_PREFIX,
            f"本会话 write_scope_root={scope}。",
            "所有写文件类工具（write_text、patch_file、write_evolve、generate_image 落盘等）"
            f"只能落在该目录下；稳定树与其它路径只读参考。",
            "壳 UI 改动在 sandbox 的 desktop/；用侧栏「壳预览」看 5174。",
            "合入稳定前在 sandbox worktree 内 commit，再 check-stable-before-sandbox-merge.bat。",
        ]
    )
