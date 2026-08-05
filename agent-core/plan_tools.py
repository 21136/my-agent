"""Plan Agent tool policy (PLAN-ARCH A11 / §15.11 C7 · Phase 38 T-3804).

查/跑与主 Agent 同权；写 TASKS/MAP/PROJECT/ENV 须提案+采纳，禁止直写落盘。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paths import AgentPaths
from plan_patch import PLAN_PATCH_ALLOWLIST
from project_mode import project_dir
from tools.schema import ToolErrorCode, ToolResult, tool_fail

# Builtins Plan may call with the same rights as the main agent.
PLAN_QUERY_TOOLS = frozenset(
    {"read_file", "list_dir", "glob_file_search", "grep", "web_search", "fetch_url"}
)
# Evolved shell — same as main; results stay on Plan transcript (caller responsibility).
PLAN_RUN_TOOLS = frozenset({"run_command"})
# Evolved writes that may touch disk — plan-domain basenames still gated.
PLAN_WRITE_TOOLS = frozenset({"write_text", "append_text", "copy_move", "patch_file"})

_PLAN_DOMAIN = frozenset(PLAN_PATCH_ALLOWLIST)


def classify_plan_tool(name: str) -> str:
    """Return query | run | write | denied."""
    n = (name or "").strip()
    if n in PLAN_QUERY_TOOLS:
        return "query"
    if n in PLAN_RUN_TOOLS:
        return "run"
    if n in PLAN_WRITE_TOOLS:
        return "write"
    if n in {"run_evolved", "write_evolve", "report_progress"}:
        return "denied"
    return "denied"


def _basename_of_path_arg(path_arg: str) -> str:
    raw = str(path_arg or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return Path(raw).name


def is_plan_domain_write_target(
    paths: AgentPaths,
    project_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> bool:
    """True when a write tool would mutate TASKS/MAP/PROJECT/ENV under the project."""
    if classify_plan_tool(tool_name) != "write":
        return False
    args = dict(arguments or {})
    candidates: list[str] = []
    for key in ("path", "dst", "to", "target"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            candidates.append(val)
    src = args.get("src") or args.get("from")
    if isinstance(src, str) and src.strip() and tool_name == "copy_move":
        # copy_move may overwrite dst; gate on destination only
        pass
    root = project_dir(paths, project_id).resolve()
    for raw in candidates:
        name = _basename_of_path_arg(raw)
        if name in _PLAN_DOMAIN:
            try:
                # Prefer resolve under agent; fall back to basename match
                from tools.builtin.read_file import resolve_read_path

                resolved = resolve_read_path(paths, raw).resolve()
                if resolved.parent == root and resolved.name in _PLAN_DOMAIN:
                    return True
            except Exception:
                # Bare name or unresolvable — still treat basename as plan-domain.
                return True
    return False


def plan_domain_write_blocked_message(tool_name: str, path_hint: str = "") -> str:
    hint = f" ({path_hint})" if path_hint else ""
    return (
        f"计划域四件套不可经 {tool_name} 直写{hint}；"
        "请提 patch/add 提案，侧栏采纳后才落盘。"
    )


def execute_plan_tool(
    paths: AgentPaths,
    project_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    confirm_fn: Any | None = None,
) -> ToolResult:
    """Run a Plan-channel tool under A11 policy.

    Query/run: same runners as main Agent.
    Write to plan-domain four files: fail closed (须门).
    Other writes (业务 / bugs/): allowed via ToolExecutor when available.
    """
    name = (tool_name or "").strip()
    args = dict(arguments or {})
    kind = classify_plan_tool(name)

    if kind == "denied":
        return tool_fail(
            name or "unknown",
            ToolErrorCode.TOOL_NOT_FOUND,
            f"Plan 通道不允许工具「{name}」",
        )

    if kind == "write" and is_plan_domain_write_target(paths, project_id, name, args):
        path_hint = str(args.get("path") or args.get("dst") or "")
        return tool_fail(
            name,
            ToolErrorCode.PERMISSION_DENIED,
            plan_domain_write_blocked_message(name, path_hint),
            details={"plan_domain_gate": True, "path": path_hint},
        )

    if kind == "query":
        return _run_builtin(paths, name, args, project_id=project_id)

    # run + non-domain write: go through ToolExecutor (auto-approve for Plan channel)
    return _run_via_executor(paths, name, args, confirm_fn=confirm_fn)


_PLAN_QUERY_BASENAMES = frozenset(PLAN_PATCH_ALLOWLIST) | frozenset({"TASKS.archive.md"})


def _resolve_plan_query_path(
    paths: AgentPaths,
    project_id: str,
    path_arg: str,
) -> str:
    """Map bare TASKS.md / MAP.md / TASKS.archive.md to workspace/{project_id}/."""
    raw = str(path_arg or "").strip()
    if not raw or not (project_id or "").strip():
        return raw
    norm = raw.replace("\\", "/")
    name = Path(norm).name
    if name not in _PLAN_QUERY_BASENAMES:
        return raw
    pid = project_id.strip()
    prefix = f"workspace/{pid}/"
    if norm.startswith(prefix) or norm == f"workspace/{pid}":
        return raw
    candidate = project_dir(paths, pid) / name
    if candidate.is_file():
        return paths.to_agent_relative(candidate)
    # Prefer project path even when missing — clearer errors than agent-root miss
    return prefix + name


def _coerce_plan_tool_args(
    paths: AgentPaths,
    project_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    if tool_name not in PLAN_QUERY_TOOLS:
        return args
    out = dict(args)
    for key in ("path", "root", "directory"):
        val = out.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = _resolve_plan_query_path(paths, project_id, val)
    return out


def _run_builtin(
    paths: AgentPaths,
    name: str,
    args: dict[str, Any],
    *,
    project_id: str = "",
) -> ToolResult:
    from tools.builtin import fetch_url, grep, list_dir, read_file, web_search

    runners = {
        "read_file": read_file.run,
        "list_dir": list_dir.run,
        "grep": grep.run,
        "web_search": web_search.run,
        "fetch_url": fetch_url.run,
    }
    runner = runners.get(name)
    if runner is None:
        return tool_fail(name, ToolErrorCode.TOOL_NOT_FOUND, f"unknown query tool: {name}")
    coerced = _coerce_plan_tool_args(paths, project_id, name, args)
    return runner(coerced, paths=paths)


def _run_via_executor(
    paths: AgentPaths,
    name: str,
    args: dict[str, Any],
    *,
    confirm_fn: Any | None = None,
) -> ToolResult:
    from tools.executor import ExecutorSession, ToolExecutor
    from tools.registry import ToolRegistry

    def _auto_yes(preview: str, allow_approve_all: bool = False) -> str:
        void = (preview, allow_approve_all)
        del void
        return "y"

    registry = ToolRegistry.load(paths)
    # Plan 查跑同权：开放 run_command + 非计划域写工具
    allowed = set(PLAN_RUN_TOOLS) | set(PLAN_WRITE_TOOLS)
    session = ExecutorSession(allowed_evolved=allowed, project_id=str(project_id or ""))
    executor = ToolExecutor(
        registry=registry,
        session=session,
        confirm_fn=confirm_fn or _auto_yes,
    )

    if name in PLAN_RUN_TOOLS or name in PLAN_WRITE_TOOLS:
        return executor.run(
            "run_evolved",
            {"tool_name": name, "arguments": args},
        )
    return tool_fail(name, ToolErrorCode.TOOL_NOT_FOUND, f"unsupported plan tool: {name}")
