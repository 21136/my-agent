import { escapeHtml } from "../chat-state";

type ProcessTool = {
  tool: string;
  summary: string;
  status: "running" | "ok" | "fail";
  endSummary?: string;
  logsTail?: string;
  callId: string;
  startedAt: number;
  endedAt?: number;
};

type ProcessBlockLike = {
  collapsed: boolean;
  tools?: ProcessTool[];
  turnKey: string;
  llmPending?: boolean;
  reasoningPhase?: "idle" | "streaming" | "pinned";
};

const ADOPT_NOTICE_RE = /^已采纳写入\s+(\S+)$/;

export function isAdoptNotice(text: string): boolean {
  return ADOPT_NOTICE_RE.test(text.trim());
}

export function adoptPathFromNotice(text: string): string | null {
  const m = text.trim().match(ADOPT_NOTICE_RE);
  return m?.[1] ?? null;
}

/** 工具行主文案：人话动作 + 文件名 */
export function humanizeToolAction(tool: string, summary: string, endSummary?: string): string {
  const blob = `${summary} ${endSummary ?? ""}`;
  const pathMatch = blob.match(/(?:^|\s)((?:[\w.-]+[/\\])?[\w.-]+\.(?:md|mjs|js|ts|tsx|py|json|css|html|vue|toml|yaml|yml))(?:\s|$)/i)
    || blob.match(/(workspace[/\\][^\s,;]+)/);
  const file = pathMatch
    ? pathMatch[1].replace(/\\/g, "/").split("/").pop() || pathMatch[1]
    : "";

  const t = tool.toLowerCase();
  if (t.includes("patch_file") || t.includes("write_utf8") || t.includes("write_file") || t.includes("write_evolve")) {
    return file ? `写入 ${file}` : "写入文件";
  }
  if (t.includes("read_file") || t.includes("read_utf8")) {
    return file ? `读取 ${file}` : "读取文件";
  }
  if (t.includes("run_command") || t.includes("run_project")) {
    return file ? `运行 ${file}` : "运行命令";
  }
  if (t.includes("glob_file")) return "搜索文件";
  if (t.includes("plan_partner")) return "整理计划";
  const short = tool.replace(/^run_evolved\s+/, "").replace(/^run_/, "");
  return file ? `${short} · ${file}` : short;
}

export function humanizeToolSubline(
  status: ProcessTool["status"],
  endSummary?: string,
): string {
  if (status === "fail" && endSummary) {
    return endSummary
      .replace(/^find anchor not found/i, "锚点未找到")
      .replace(/^patch\s+/i, "")
      .slice(0, 120);
  }
  return "";
}

export function formatReasoningDisplay(text: string): string {
  return text
    .replace(/\^\^([^\n^]+?)\^\^/g, "$1")
    .replace(/\^\^/g, "")
    .trim();
}

export function reasoningSummaryLine(text: string, max = 56): string {
  const normalized = formatReasoningDisplay(text);
  const firstLine = normalized.split(/\n/).map((line) => line.trim()).find(Boolean) ?? "";
  if (!firstLine) return "";
  if (firstLine.length <= max) return firstLine;
  return `${firstLine.slice(0, max - 1)}…`;
}

export function processPillLabel(
  block: ProcessBlockLike,
  opts?: { expanded?: boolean },
): string {
  const tools = block.tools ?? [];
  const n = tools.length;
  const running = tools.some((t) => t.status === "running");
  const fails = tools.filter((t) => t.status === "fail").length;
  const thinkingLive =
    Boolean(block.llmPending) || block.reasoningPhase === "streaming";
  const expanded = opts?.expanded ?? !block.collapsed;
  if (thinkingLive && n === 0) {
    return expanded ? "过程" : "思考中…";
  }
  const parts: string[] = [];
  parts.push(n > 0 ? `过程 · ${n} 步` : "过程");
  if (thinkingLive && !expanded) parts.push("思考中");
  if (running) parts.push("进行中");
  else if (fails > 0) parts.push(`${fails} 失败`);
  return parts.join(" · ");
}

export function lastFailedTool(tools: ProcessTool[] | undefined): ProcessTool | undefined {
  if (!tools?.length) return undefined;
  for (let i = tools.length - 1; i >= 0; i--) {
    if (tools[i].status === "fail") return tools[i];
  }
  return undefined;
}

export function renderToolFailAlert(tool: ProcessTool | undefined): string {
  if (!tool) return "";
  const label = humanizeToolAction(tool.tool, tool.summary, tool.endSummary);
  const detail = humanizeToolSubline("fail", tool.endSummary) || "执行失败";
  const logs = tool.logsTail
    ? `<details class="unified-tool-alert-logs"><summary>查看日志</summary><pre>${escapeHtml(tool.logsTail)}</pre></details>`
    : "";
  return `<div class="unified-tool-alert" role="alert">
    <div class="unified-tool-alert-title">修补未成功</div>
    <p class="unified-tool-alert-body">${escapeHtml(label)} — ${escapeHtml(detail)}</p>
    ${logs}
  </div>`;
}

export function renderAdoptChip(path: string): string {
  const base = path.split(/[/\\]/).pop() || path;
  return `<span class="unified-file-chip is-ok" title="${escapeHtml(path)}">${escapeHtml(base)}</span>`;
}

export function renderReviewCallout(title: string, body: string, running: boolean): string {
  const bodyHtml = running
    ? `<p class="unified-review-callout-body">${escapeHtml(body)}</p>`
    : `<ul class="unified-review-callout-list">${body
        .split(/\n+/)
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const p0 = /^p0\b/i.test(line) || /blocker/i.test(line);
          const tag = p0 ? `<span class="unified-tag-p0">P0</span> ` : "";
          return `<li>${tag}${escapeHtml(line.replace(/^p0\s*blocker[:\s]*/i, ""))}</li>`;
        })
        .join("")}</ul>`;
  return `<article class="unified-review-callout">
    <div class="unified-review-callout-title">${escapeHtml(title)}</div>
    ${bodyHtml}
  </article>`;
}
