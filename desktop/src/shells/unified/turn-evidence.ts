import type { TurnEvidenceItem, TurnEvidenceTestSummary } from "../../api/ws";

export interface TurnEvidenceSnapshot {
  runId: string;
  items: TurnEvidenceItem[];
  totalCount: number;
  omittedCount: number;
}

const TEST_STATUSES = new Set(["passed", "failed", "blocked", "timeout"]);
const TOOL_LABELS: Record<string, string> = {
  structured_test: "测试验证",
  interactive_terminal: "终端会话",
  git_snapshot: "检查变更",
  git_diff: "查看差异",
  git_restore: "恢复变更",
  run_command: "运行命令",
  patch_file: "修改文件",
  write_text: "写入文件",
};

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\"", "&quot;");
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function shortText(value: unknown, max = 240): string | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return text ? text.slice(0, max) : undefined;
}

function normalizeTest(value: unknown): TurnEvidenceTestSummary | undefined {
  const record = asRecord(value);
  if (!record || typeof record.status !== "string" || !TEST_STATUSES.has(record.status)) return undefined;
  const failures = Array.isArray(record.failed)
    ? record.failed.slice(0, 3).flatMap((raw) => {
      const item = asRecord(raw);
      if (!item) return [];
      const failure: TurnEvidenceTestSummary["failed"][number] = {};
      const file = shortText(item.file, 200);
      const test = shortText(item.test, 160);
      if (file) failure.file = file;
      if (typeof item.line === "number" && Number.isSafeInteger(item.line) && item.line > 0) failure.line = item.line;
      if (test) failure.test = test;
      return [failure];
    })
    : [];
  const summary: TurnEvidenceTestSummary = { status: record.status as TurnEvidenceTestSummary["status"], failed: failures };
  const suite = shortText(record.suite, 80);
  const reason = shortText(record.reason, 80);
  if (suite) summary.suite = suite;
  if (reason) summary.reason = reason;
  if (typeof record.duration_ms === "number" && Number.isSafeInteger(record.duration_ms) && record.duration_ms >= 0) {
    summary.duration_ms = record.duration_ms;
  }
  return summary;
}

function normalizeItem(value: unknown): TurnEvidenceItem | null {
  const record = asRecord(value);
  if (!record) return null;
  const tool = shortText(record.tool, 80);
  if (!tool || typeof record.ok !== "boolean") return null;
  const item: TurnEvidenceItem = { tool, ok: record.ok };
  const callId = shortText(record.call_id, 128);
  const terminalId = shortText(record.terminal_session_id, 128);
  const terminalState = shortText(record.terminal_state, 32);
  const diffSummary = shortText(record.diff_summary, 320);
  if (callId) item.call_id = callId;
  if (terminalId) item.terminal_session_id = terminalId;
  if (terminalState) item.terminal_state = terminalState;
  if (diffSummary) item.diff_summary = diffSummary;
  if (Array.isArray(record.paths)) {
    item.paths = record.paths.slice(0, 8).flatMap((path) => {
      const text = shortText(path, 240);
      return text ? [text] : [];
    });
  }
  const test = normalizeTest(record.test);
  if (test) item.test = test;
  return item;
}

export function normalizeTurnEvidenceEvent(value: unknown, currentRunId: string): TurnEvidenceSnapshot | null {
  const record = asRecord(value);
  const runId = shortText(record?.run_id, 128);
  if (!record || !runId || !currentRunId || runId !== currentRunId) return null;
  const items = Array.isArray(record.items)
    ? record.items.slice(-20).flatMap((item) => {
      const normalized = normalizeItem(item);
      return normalized ? [normalized] : [];
    })
    : [];
  const totalCount = typeof record.total_count === "number" && Number.isSafeInteger(record.total_count)
    ? Math.max(items.length, record.total_count)
    : items.length;
  const omittedCount = typeof record.omitted_count === "number" && Number.isSafeInteger(record.omitted_count)
    ? Math.max(0, record.omitted_count)
    : Math.max(0, totalCount - items.length);
  return { runId, items, totalCount, omittedCount };
}

function visiblePath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const segments = normalized.split("/").filter(Boolean);
  return segments.slice(-3).join("/") || path;
}

function testStatusLabel(status: TurnEvidenceTestSummary["status"]): string {
  if (status === "passed") return "测试通过";
  if (status === "failed") return "测试未通过";
  if (status === "timeout") return "测试超时";
  return "未能验证";
}

function runSummaryLabel(state: {
  running: boolean;
  finishReason: string;
  items: TurnEvidenceItem[];
}): string {
  if (state.running) return "运行中";
  if (["cancelled", "timeout", "error", "tool_error", "tool_loop_exceeded", "segment_cap_pause"].includes(state.finishReason)) {
    return "本轮未完成";
  }
  const tests = state.items.flatMap((item) => item.test ? [item.test.status] : []);
  if (tests.includes("failed")) return "测试未通过";
  if (tests.includes("timeout")) return "测试超时";
  if (tests.includes("blocked")) return "未能验证";
  if (state.items.some((item) => !item.ok)) return "有操作失败";
  if (tests.includes("passed")) return "测试通过";
  return "未提供测试证据";
}

export function renderTurnEvidenceSummary(state: {
  runId: string;
  items: TurnEvidenceItem[];
  totalCount: number;
  omittedCount: number;
  running: boolean;
  finishReason: string;
  expanded: boolean;
}): string {
  if (!state.runId || state.items.length === 0) return "";
  const resultLabel = runSummaryLabel(state);
  const countLabel = `${state.totalCount} 项操作`;
  const rows = state.items.map((item) => {
    const label = item.test
      ? testStatusLabel(item.test.status)
      : item.ok ? "操作完成" : "操作失败";
    const statusClass = item.test
      ? `is-${escapeHtml(item.test.status)}`
      : item.ok ? "is-passed" : "is-failed";
    const paths = item.paths?.length
      ? `<div class="sidebar-turn-summary-paths">${item.paths.slice(0, 3).map((path) => `<code>${escapeHtml(visiblePath(path))}</code>`).join("")}${item.paths.length > 3 ? `<span>另有 ${item.paths.length - 3} 个文件</span>` : ""}</div>`
      : "";
    const diff = item.diff_summary
      ? `<div class="sidebar-turn-summary-diff">变更：${escapeHtml(item.diff_summary)}</div>`
      : "";
    const failures = item.test?.failed.length
      ? `<ul class="sidebar-turn-summary-failures">${item.test.failed.map((failure) => {
        const location = failure.file ? visiblePath(failure.file) : "测试";
        const line = failure.line ? `:${failure.line}` : "";
        const name = failure.test ? ` · ${failure.test}` : "";
        return `<li><code>${escapeHtml(location + line)}</code>${escapeHtml(name)}</li>`;
      }).join("")}</ul>`
      : "";
    const testMeta = item.test?.suite
      ? `<span class="sidebar-turn-summary-meta">${escapeHtml(item.test.suite)}${item.test.duration_ms !== undefined ? ` · ${(item.test.duration_ms / 1000).toFixed(1)} 秒` : ""}</span>`
      : "";
    const terminal = item.terminal_session_id
      ? `<button type="button" class="sidebar-turn-summary-link" data-action="turn-evidence-terminal" data-terminal-id="${escapeHtml(item.terminal_session_id)}">查看终端</button>`
      : "";
    const jump = item.call_id
      ? `<button type="button" class="sidebar-turn-summary-link" data-action="turn-evidence-jump" data-call-id="${escapeHtml(item.call_id)}">查看记录</button>`
      : "";
    return `<li class="sidebar-turn-summary-item ${statusClass}">
      <div class="sidebar-turn-summary-row"><strong>${escapeHtml(TOOL_LABELS[item.tool] || item.tool)}</strong><span>${escapeHtml(label)}</span></div>
      ${testMeta}${paths}${diff}${failures}
      ${(terminal || jump) ? `<div class="sidebar-turn-summary-actions">${terminal}${jump}</div>` : ""}
    </li>`;
  }).join("");
  const omitted = state.omittedCount > 0
    ? `<div class="sidebar-turn-summary-omitted">还有 ${state.omittedCount} 项操作未展开</div>`
    : "";
  const details = state.expanded && !state.running
    ? `<div class="sidebar-turn-summary-details">${rows ? `<ul>${rows}</ul>` : `<div class="sidebar-turn-summary-empty">本轮没有工具证据</div>`}${omitted}</div>`
    : "";
  return `<div class="sidebar-turn-summary-wrap" data-run-id="${escapeHtml(state.runId)}">
    <button type="button" class="sidebar-turn-summary" data-action="toggle-turn-evidence" aria-expanded="${state.expanded && !state.running ? "true" : "false"}" ${state.running ? "disabled" : ""}>
      <span class="sidebar-turn-summary-chevron" aria-hidden="true">${state.expanded ? "▾" : "▸"}</span>
      <span class="sidebar-turn-summary-text">本回合 · ${escapeHtml(countLabel)} · ${escapeHtml(resultLabel)}</span>
      <span class="sidebar-turn-summary-jump" aria-hidden="true">${state.expanded ? "⌃" : "⌄"}</span>
    </button>
    ${details}
  </div>`;
}
