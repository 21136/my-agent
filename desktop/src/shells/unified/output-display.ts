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

const TOOL_ERROR_RULES: Array<[RegExp, string]> = [
  [/find anchor not found/i, "没有找到要替换的原文"],
  [/anchor not found/i, "没有找到要替换的原文"],
  [/anchor matched \d+ times/i, "原文出现了多处，无法确定改哪一处"],
  [/must be unique/i, "原文不唯一，无法确定改哪一处"],
  [/patch_id was already applied but the file now has a different hash/i, "文件内容和上次修改后不一致，请重新读取再改"],
  [/base_hash mismatch/i, "文件在准备修改之后又变了，请先重新读取"],
  [/file changed while patch was being prepared/i, "写入前文件又变了，请重新读取再改"],
  [/patch_id already belongs to/i, "这个修改编号已经用在别的文件上"],
  [/patch_id is already associated with a different patch payload/i, "这个修改编号对应另一处改动"],
  [/patch_id ledger conflict/i, "修改记录冲突，请重试"],
  [/patch or ledger write failed/i, "写入失败"],
  [/binary file not supported/i, "不能修改二进制文件"],
  [/file is not valid UTF-8/i, "文件不是 UTF-8 文本"],
  [/file exceeds limit/i, "文件太大，没法修改"],
  [/result exceeds limit/i, "改完的文件太大"],
  [/path is required/i, "缺少文件路径"],
  [/not a file:/i, "这不是一个文件"],
  [/replacement is required/i, "缺少要写入的新内容"],
  [/provide start_line\+end_line or find/i, "请指定要改的行，或给出要查找的原文"],
  [/hunks cannot be combined/i, "不能同时用多种改法"],
];

function latinRatio(text: string): number {
  const letters = text.match(/[A-Za-z]/g)?.length ?? 0;
  return letters / Math.max(text.length, 1);
}

/** Tool-card failure text. Known internal English becomes a short Chinese line. */
export function userFacingToolError(raw: string): string {
  const text = raw.trim();
  if (!text) return "";
  for (const [pattern, message] of TOOL_ERROR_RULES) {
    if (pattern.test(text)) return message;
  }
  if (latinRatio(text) > 0.45) {
    const path = text.match(/(workspace[/\\][^\s,;]+)/);
    return path ? `这一步没有完成（${path[1]}）` : "这一步没有完成";
  }
  return text.slice(0, 160);
}

export function humanizeToolSubline(
  status: ProcessTool["status"],
  endSummary?: string,
): string {
  const text = (endSummary || "").trim();
  if (!text || text === "ok" || text === "完成") return "";
  if (status === "fail") return userFacingToolError(text).slice(0, 160);
  if (status === "ok") return text.slice(0, 160);
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
