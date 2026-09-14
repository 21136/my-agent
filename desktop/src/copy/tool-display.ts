/**
 * User-facing tool name aliases.
 * LLM / WS protocol keep canonical ids (run_evolved, write_text, …); UI calls this layer only.
 */

import { getToolDisplayOverride } from "./tool-display-overrides";
export type ToolDisplayContext = {
  summary?: string;
  endSummary?: string;
};

/** Canonical tool id → short user label (noun). */
export const TOOL_DISPLAY_NAMES: Record<string, string> = {
  run_evolved: "扩展工具",
  write_text: "写入文件",
  write_utf8_text: "写入文件",
  write_file: "写入文件",
  write_evolve: "写入进化区",
  patch_file: "修补文件",
  read_file: "读取文件",
  read_utf8_text: "读取文件",
  grep: "搜索内容",
  glob_file_search: "搜索文件",
  list_dir: "列出目录",
  run_command: "运行命令",
  run_project_tests: "运行项目测试",
  run_quality: "质量检查",
  run_service: "托管服务",
  plan_partner: "整理计划",
  explore: "探索代码库",
  codebase_search: "语义搜索",
  web_search: "网页搜索",
  fetch_url: "获取网页",
  report_progress: "更新任务进度",
  deliverable_review: "交付检查",
  scaffold_project: "项目脚手架",
  db_migrate_status: "数据库迁移",
  http_request: "HTTP 请求",
  browser_open: "打开浏览器",
  propose_context_switch: "切换上下文",
  host_read: "读取托管区",
  host_grep: "搜索托管区",
  host_list: "列出托管区",
  host_copy_move: "托管区复制",
  copy_move: "复制或移动",
  append_text: "追加文本",
  move_to_trash: "移入回收站",
  pip_install: "安装依赖",
  git_commit: "Git 提交",
  git_push: "Git 推送",
  git_branch: "Git 分支",
  git_snapshot: "Git 快照",
  git_clone: "Git 克隆",
  run_python: "运行 Python",
  run_demo: "运行演示",
  dev_start: "启动开发环境",
  repair_node_modules: "修复依赖",
  db_query: "数据库查询",
  evolved: "扩展工具",
};

/** Verb for one-line activity timeline (verb + target). */
const TOOL_ACTION_VERBS: Record<string, string> = {
  write_text: "写入",
  write_utf8_text: "写入",
  write_file: "写入",
  write_evolve: "写入",
  patch_file: "修补",
  read_file: "读取",
  read_utf8_text: "读取",
  grep: "搜索",
  glob_file_search: "查找",
  list_dir: "列出",
  run_command: "运行",
  run_project_tests: "测试",
  run_quality: "检查",
  run_service: "服务",
  plan_partner: "整理计划",
  explore: "探索",
  codebase_search: "搜索",
  web_search: "搜索",
  fetch_url: "获取",
  report_progress: "更新进度",
  deliverable_review: "交付检查",
  host_read: "读取",
  host_grep: "搜索",
  append_text: "追加",
  evolved: "写入",
};

const FILE_EXT_RE = /([\w.-]+\.(?:md|mjs|js|ts|tsx|py|json|css|html|vue|toml|yaml|yml|ps1|sh|bat))(?:\s|$)/i;
const WORKSPACE_RE = /(workspace[/\\][^\s,;]+)/;

export function parseEvolvedToolFromSummary(summary: string): string | null {
  const trimmed = summary.trim();
  const colon = trimmed.match(/^([a-z][\w]*)\s*:\s*/i);
  if (colon) return colon[1].toLowerCase();
  return null;
}

/** Resolve inner evolved tool id when wrapper is run_evolved / evolved. */
export function resolveEffectiveToolName(tool: string, summary?: string): string {
  const raw = tool.trim();
  const lower = raw.toLowerCase();
  if (lower === "run_evolved" || lower === "evolved") {
    const inner = summary ? parseEvolvedToolFromSummary(summary) : null;
    if (inner) return inner;
    return lower === "evolved" ? "evolved" : "run_evolved";
  }
  const suffix = raw.match(/^run_evolved[.\s]+([a-z][\w]*)$/i);
  if (suffix) return suffix[1].toLowerCase();
  if (lower.startsWith("run_")) return lower.slice(4);
  return lower;
}

function extractFileHint(blob: string): string {
  const pathPart = blob.includes(":") ? blob.split(":").slice(1).join(":").trim() : blob;
  const pathMatch = pathPart.match(FILE_EXT_RE) || pathPart.match(WORKSPACE_RE)
    || blob.match(FILE_EXT_RE) || blob.match(WORKSPACE_RE);
  if (!pathMatch) return "";
  return pathMatch[1];
}

export function builtinToolDisplayName(tool: string): string {
  const lower = tool.trim().toLowerCase();
  return TOOL_DISPLAY_NAMES[lower] ?? TOOL_DISPLAY_NAMES[lower.replace(/^run_/, "")] ?? lower;
}

function resolveDisplayLabel(tool: string, ctx?: ToolDisplayContext): string {
  const blob = `${ctx?.summary ?? ""} ${ctx?.endSummary ?? ""}`;
  const effective = resolveEffectiveToolName(tool, blob.trim() || ctx?.summary);
  const override = getToolDisplayOverride(effective);
  if (override) return override;
  return builtinToolDisplayName(effective);
}

export function displayToolName(tool: string, ctx?: ToolDisplayContext): string {
  return resolveDisplayLabel(tool, ctx);
}

/** Activity timeline / fail alert one-liner. */
export function formatToolActionLabel(tool: string, summary: string, endSummary?: string): string {
  const blob = `${summary} ${endSummary ?? ""}`;
  const effective = resolveEffectiveToolName(tool, summary || endSummary);
  const override = getToolDisplayOverride(effective);
  const file = extractFileHint(blob);
  if (override) return file ? `${override} ${file}` : override;
  const verb = TOOL_ACTION_VERBS[effective];
  if (verb && file) return `${verb} ${file}`;
  if (verb) return TOOL_DISPLAY_NAMES[effective] ?? verb;
  const label = displayToolName(tool, { summary, endSummary });
  return file ? `${label} · ${file}` : label;
}
