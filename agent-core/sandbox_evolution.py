"""Sandbox worktree helpers for self-evolution sessions (SANDBOX-EVOLUTION)."""

from __future__ import annotations

import os
from pathlib import Path

from paths import AgentPaths, PathError

SANDBOX_WRITE_SCOPE_DEFAULT = ".worktrees/sandbox"
_SEED_PREFIX = "[实验进化会话]"


def _sandbox_tree_ready(root: Path) -> bool:
    if not root.is_dir():
        return False
    markers = (
        root / "evolve" / "_index.core.toml",
        root / "evolve" / "_index.toml",
    )
    return any(m.is_file() for m in markers)


def resolve_sandbox_root(paths: AgentPaths) -> Path:
    """Absolute path to the sandbox git worktree (may not exist)."""
    raw = (os.environ.get("MY_AGENT_SANDBOX_ROOT") or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = paths.agent_root / candidate
        return candidate.resolve()
    nested = (paths.agent_root / ".worktrees" / "sandbox").resolve()
    if _sandbox_tree_ready(nested):
        return nested
    if _sandbox_tree_ready(paths.agent_root):
        return paths.agent_root.resolve()
    return nested


def sandbox_worktree_ready(paths: AgentPaths) -> bool:
    """True when sandbox exists and looks like an agent root."""
    return _sandbox_tree_ready(resolve_sandbox_root(paths))


def is_sandbox_agent_root(paths: AgentPaths) -> bool:
    """True when this process/agent root is the sandbox worktree (dual sidecar lane)."""
    root = resolve_sandbox_root(paths)
    if not root.is_dir():
        return False
    try:
        return paths.agent_root.resolve() == root.resolve()
    except OSError:
        return False


def sandbox_write_scope_relative(paths: AgentPaths) -> str:
    """POSIX-style path under agent root for session write_scope_root."""
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


def normalize_write_scope_root(raw: str) -> str:
    text = raw.strip().replace("\\", "/").strip("/")
    if not text or ".." in text.split("/"):
        raise PathError(f"非法写入范围：{raw!r}")
    return text


def write_scope_label(scope_root: str, *, paths: AgentPaths | None = None) -> str:
    if paths is not None and is_sandbox_agent_root(paths):
        return "运行在 sandbox agent-core · 稳定 5173 / 8765 不受影响"
    scope = normalize_write_scope_root(scope_root) if scope_root.strip() else ""
    if not scope:
        return ""
    return f"写入仅限 {scope} · 执行用稳定 agent-core"


def sandbox_session_seed_content(scope_root: str, *, paths: AgentPaths | None = None) -> str:
    if paths is not None and is_sandbox_agent_root(paths):
        return "\n".join(
            [
                _SEED_PREFIX,
                "本 sidecar 根目录即 sandbox worktree；运行 **sandbox 里的 agent-core 与工具**（含你新加的 builtin）。",
                "稳定主壳 5173 与稳定 sidecar（8765）并行运行，互不占会话锁。",
                "改壳/改工具直接写本树；用「壳预览」5174 验收 UI。",
                "实验做坏了可开稳定会话或 Cursor 修 sandbox；合入前稳定树 product 代码仍不变。",
                "满意后 sandbox commit → 壳预览「一键合入」→「同步 sandbox」。",
            ]
        )
    scope = normalize_write_scope_root(scope_root)
    return "\n".join(
        [
            _SEED_PREFIX,
            f"本会话 write_scope_root={scope}。",
            "运行时使用稳定版 agent-core 与工具集；所有写盘（write_text、patch_file、write_evolve、generate_image 等）"
            f"只能落在 {scope} 下。仓库根目录的 agent-core/desktop 只读，稳定 5173 不受影响。",
            "要在 sandbox 里**试跑新工具/新 agent-core**：侧栏壳预览 →「开实验进化会话」会连 **sandbox sidecar（8766）** 单独试跑。",
            "新功能/改壳在 sandbox 的 desktop/、agent-core/ 里改；用侧栏「壳预览」在 5174 验收。",
            "不合入则稳定保持原样；5174 挂了也不应拖垮 5173（勿长期「采用预览为主壳」）。",
            "稳定侧可用 Cursor 或普通会话只读排查，在 sandbox 路径里修实验版。",
            "满意后在 sandbox commit，再壳预览「一键合入」→「同步 sandbox」；或 check-stable-before-sandbox-merge.bat。",
        ]
    )
