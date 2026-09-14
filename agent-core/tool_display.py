"""User-facing tool name aliases (desktop / confirm UI). LLM tool ids stay canonical."""

from __future__ import annotations

import re

_TOOL_DISPLAY_NAMES: dict[str, str] = {
    "run_evolved": "扩展工具",
    "write_text": "写入文件",
    "write_utf8_text": "写入文件",
    "write_file": "写入文件",
    "write_evolve": "写入进化区",
    "patch_file": "修补文件",
    "read_file": "读取文件",
    "read_utf8_text": "读取文件",
    "grep": "搜索内容",
    "glob_file_search": "搜索文件",
    "list_dir": "列出目录",
    "run_command": "运行命令",
    "run_project_tests": "运行项目测试",
    "run_quality": "质量检查",
    "run_service": "托管服务",
    "plan_partner": "整理计划",
    "explore": "探索代码库",
    "codebase_search": "语义搜索",
    "web_search": "网页搜索",
    "fetch_url": "获取网页",
    "report_progress": "更新任务进度",
    "deliverable_review": "交付检查",
    "scaffold_project": "项目脚手架",
    "evolved": "扩展工具",
}

_TOOL_ACTION_VERBS: dict[str, str] = {
    "write_text": "写入",
    "write_utf8_text": "写入",
    "patch_file": "修补",
    "read_file": "读取",
    "grep": "搜索",
    "glob_file_search": "查找",
    "list_dir": "列出",
    "run_command": "运行",
    "plan_partner": "整理计划",
    "explore": "探索",
    "evolved": "写入",
}

_EVOLVED_SUMMARY_RE = re.compile(r"^([a-z][\w]*)\s*:\s*", re.IGNORECASE)
_FILE_HINT_RE = re.compile(
    r"([\w.-]+\.(?:md|mjs|js|ts|tsx|py|json|css|html|vue|toml|yaml|yml|ps1|sh|bat))(?:\s|$)",
    re.IGNORECASE,
)


def parse_evolved_tool_from_summary(summary: str) -> str | None:
    match = _EVOLVED_SUMMARY_RE.match(summary.strip())
    return match.group(1).lower() if match else None


def resolve_effective_tool_name(tool: str, summary: str = "") -> str:
    raw = tool.strip()
    lower = raw.lower()
    if lower in {"run_evolved", "evolved"}:
        inner = parse_evolved_tool_from_summary(summary) if summary else None
        if inner:
            return inner
        return lower
    if lower.startswith("run_"):
        return lower[4:]
    return lower


def display_tool_name(tool: str, *, summary: str = "", end_summary: str = "") -> str:
    blob = f"{summary} {end_summary}".strip()
    effective = resolve_effective_tool_name(tool, blob or summary)
    return _TOOL_DISPLAY_NAMES.get(effective, _TOOL_DISPLAY_NAMES.get(effective.replace("run_", ""), effective))


def _extract_file_hint(blob: str) -> str:
    path_part = blob
    if ":" in blob:
        path_part = blob.split(":", 1)[1].strip()
    file_match = _FILE_HINT_RE.search(path_part) or _FILE_HINT_RE.search(blob)
    if not file_match:
        return ""
    return file_match.group(1)


def format_tool_action_label(tool: str, summary: str = "", end_summary: str = "") -> str:
    blob = f"{summary} {end_summary}".strip()
    effective = resolve_effective_tool_name(tool, summary or end_summary)
    verb = _TOOL_ACTION_VERBS.get(effective)
    file_hint = _extract_file_hint(blob)
    if verb and file_hint:
        return f"{verb} {file_hint}"
    if verb:
        return _TOOL_DISPLAY_NAMES.get(effective, verb)
    label = display_tool_name(tool, summary=summary, end_summary=end_summary)
    return f"{label} · {file_hint}" if file_hint else label
