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
  explore: "探索代码库",
  codebase_search: "语义搜索",
  web_search: "网页搜索",
  fetch_url: "获取网页",
  generate_image: "生成图片",
  show_images: "展示图片",
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
  git_diff: "Git 差异",
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
  explore: "探索",
  codebase_search: "搜索",
  web_search: "搜索",
  fetch_url: "获取",
  deliverable_review: "交付检查",
  host_read: "读取",
  host_grep: "搜索",
  append_text: "追加",
  git_diff: "对比差异",
  git_snapshot: "检查状态",
  evolved: "写入",
};

const FILE_EXT_RE = /([\w.-]+\.(?:md|mjs|js|ts|tsx|py|json|css|html|vue|toml|yaml|yml|ps1|sh|bat))(?:\s|$)/i;
const WORKSPACE_RE = /(workspace[/\\][^\s,;]+)/;
const KNOWN_INNER_TOOLS = new Set([
  "write_text",
  "patch_file",
  "run_command",
  "write_evolve",
  "copy_move",
  "move_to_trash",
  "append_text",
  "run_service",
  "search_replace",
  "git_diff",
  "git_snapshot",
]);

export function parseEvolvedToolFromSummary(summary: string): string | null {
  const trimmed = summary.trim();
  const colon = trimmed.match(/^([a-z][\w]*)\s*:\s*/i);
  if (colon) return colon[1].toLowerCase();
  const head = trimmed.split(/[\s·]/)[0]?.toLowerCase() ?? "";
  if (KNOWN_INNER_TOOLS.has(head)) return head;
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

const COMPANION_TOOL_ASIDES: Record<string, string[]> = {
  read_file: ["正凑近翻看这页代码", "先瞄一眼原文件写的啥", "仔细核对一下上下文"],
  patch_file: ["挽起袖子动刀改代码", "精准替换这处逻辑", "顺手把这块修干净"],
  write_text: ["提笔把新内容落盘", "一气呵成写进文件里", "帮你把这份文件建好"],
  run_command: ["敲下回车跑一遍试试", "盯紧终端看执行结果", "实测一下稳不稳"],
  git_diff: ["瞅瞅刚才都改了哪些行", "逐行比对一下改动补丁", "复核一遍修改痕迹"],
  git_snapshot: ["盘点一下仓库当前状态", "看看工作区干不干净"],
  grep: ["在代码堆里顺藤摸瓜", "全局搜一下关键词在哪"],
  glob_file_search: ["翻翻目录找目标文件", "按名字把文件揪出来"],
  list_dir: ["扫一眼目录结构", "看看这层文件夹里有啥"],
  explore: ["派分身把周边代码摸个底", "先把来龙去脉探清楚"],
  codebase_search: ["按概念在仓库里寻宝", "搜一下相关实现在哪"],
  deliverable_review: ["戴稳墨镜做最后把关", "全面挑挑刺看能不能交付"],
  run_service: ["把后台服务拉起来盯好", "安顿好常驻进程"],
};

export function companionToolAside(tool: string, summary?: string, callId = ""): string {
  const effective = resolveEffectiveToolName(tool, summary);
  const pool = COMPANION_TOOL_ASIDES[effective];
  if (!pool || !pool.length) return "";
  let hash = 0;
  const seed = `${callId}:${summary || effective}`;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return pool[hash % pool.length];
}

/** Activity timeline / fail alert one-liner. */
export function formatToolActionLabel(tool: string, summary: string, endSummary?: string): string {
  const blob = `${summary} ${endSummary ?? ""}`;
  const effective = resolveEffectiveToolName(tool, summary || endSummary);
  const override = getToolDisplayOverride(effective);
  const file = extractFileHint(blob);
  if (override && !(override === "扩展工具" && file)) {
    return file ? `${override} ${file}` : override;
  }
  if ((effective === "run_evolved" || effective === "evolved" || override === "扩展工具") && file) {
    const lower = blob.toLowerCase();
    if (/trash|回收站|move_to_trash/.test(lower)) return `移入回收站 ${file}`;
    if (/copy_move|host_copy_move/.test(lower) && /move|移动/.test(lower)) return `移动 ${file}`;
    if (/copy_move|host_copy_move|复制/.test(lower)) return `复制 ${file}`;
    return `文件 ${file}`;
  }
  if (override) return file ? `${override} ${file}` : override;
  if (effective === "run_command") {
    const cmd = summary.trim();
    if (cmd) {
      const clipped = cmd.length > 140 ? `${cmd.slice(0, 139)}…` : cmd;
      return `运行 ${clipped}`;
    }
  }
  const verb = TOOL_ACTION_VERBS[effective];
  if (verb && file) return `${verb} ${file}`;
  if (verb) return TOOL_DISPLAY_NAMES[effective] ?? verb;
  const label = displayToolName(tool, { summary, endSummary });
  return file ? `${label} · ${file}` : label;
}
