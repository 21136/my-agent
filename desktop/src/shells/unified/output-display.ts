import { escapeHtml } from "../chat-state";
import { displayToolName, formatToolActionLabel } from "../../copy/tool-display";

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

/** 工具行主文案：人话动作 + 文件名（见 copy/tool-display.ts） */
export function humanizeToolAction(tool: string, summary: string, endSummary?: string): string {
  return formatToolActionLabel(tool, summary, endSummary);
}

export { displayToolName, formatToolActionLabel, resolveEffectiveToolName } from "../../copy/tool-display";

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
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .trim();
}

/** Collapsed think title should not leak long English reasoning (UX-030). */
export function shouldNeutralizeReasoningSummary(summary: string): boolean {
  const stripped = summary.replace(/\*\*/g, "").trim();
  if (!stripped) return true;
  const latin = (stripped.match(/[A-Za-z]/g) || []).length;
  return latin / Math.max(stripped.length, 1) > 0.55;
}

export function reasoningSummaryLine(text: string, max = 96): string {
  const normalized = formatReasoningDisplay(text);
  const firstLine = normalized.split(/\n/).map((line) => line.trim()).find(Boolean) ?? "";
  if (!firstLine) return "";
  if (firstLine.length <= max) return firstLine;
  return `${firstLine.slice(0, max - 1)}…`;
}

export function processPillLabel(
  block: ProcessBlockLike,
  opts?: { expanded?: boolean; segmentIndex?: number; segmentTotal?: number },
): string {
  const tools = block.tools ?? [];
  const n = tools.length;
  const running = tools.some((t) => t.status === "running");
  const fails = tools.filter((t) => t.status === "fail").length;
  const thinkingLive =
    Boolean(block.llmPending) || block.reasoningPhase === "streaming";
  const expanded = opts?.expanded ?? !block.collapsed;
  if (thinkingLive && n === 0) {
    if (block.llmPending) {
      return expanded ? "过程" : "等待模型…";
    }
    return expanded ? "过程" : "思考中…";
  }
  const parts: string[] = [];
  const segmentSuffix =
    (opts?.segmentTotal ?? 0) > 1 && (opts?.segmentIndex ?? 0) > 0
      ? ` (${opts!.segmentIndex}/${opts!.segmentTotal})`
      : "";
  parts.push(n > 0 ? `过程 · ${n} 步${segmentSuffix}` : `过程${segmentSuffix}`);
  if (thinkingLive && !expanded) parts.push("思考中");
  if (running) parts.push("进行中");
  else if (fails > 0) parts.push(`${fails} 失败`);
  return parts.join(" · ");
}

/** Only surface a bottom alert while the latest tool is still failed (stale failures stay in the timeline). */
export function lastFailedTool(tools: ProcessTool[] | undefined): ProcessTool | undefined {
  if (!tools?.length) return undefined;
  const last = tools[tools.length - 1];
  return last.status === "fail" ? last : undefined;
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
