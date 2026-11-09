import { isConfirmInProgressLabel } from "../chat-state.ts";
import { workInstructionChatStatusLabel } from "./system-notice-copy.ts";

export interface CurrentWorkLiveConfirmBlock {
  requestId: string;
  preview: string;
  confirmKind?: string;
  resolved?: string;
}

export interface CurrentWorkLiveWorkInstruction {
  text: string;
  state: string;
  deliveryStage?: string;
  reason?: string;
}

/** Minimal chat model slice for mirroring in-flight companion work on the current-work queue. */
export interface CurrentWorkLiveSessionInput {
  confirmPending: boolean;
  confirmOverlay: { requestId: string; preview: string; resolved?: string } | null;
  turnActive: boolean;
  executionState: string;
  blocks: ReadonlyArray<
    | { kind: "confirm"; requestId: string; preview: string; confirmKind?: string; resolved?: string }
    | { kind: "work-instruction"; text: string; state: string; deliveryStage?: string; reason?: string }
    | { kind: string }
  >;
}

export interface CurrentWorkLiveQueueEntry {
  id: string;
  title: string;
  status: string;
  detailHtml: string;
  attention: boolean;
}

const LIVE_WORK_INSTRUCTION_STATES = new Set([
  "sending",
  "queued",
  "orchestration_dispatching",
  "orchestration_active",
]);

function isLiveConfirmVisible(resolved: string | undefined): boolean {
  if (!resolved) return true;
  return isConfirmInProgressLabel(resolved);
}

function truncatePreview(text: string, max = 280): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}…`;
}

function confirmBlocks(input: CurrentWorkLiveSessionInput): CurrentWorkLiveConfirmBlock[] {
  const seen = new Set<string>();
  const rows: CurrentWorkLiveConfirmBlock[] = [];
  const push = (row: CurrentWorkLiveConfirmBlock) => {
    if (!row.requestId || seen.has(row.requestId) || !isLiveConfirmVisible(row.resolved)) return;
    seen.add(row.requestId);
    rows.push(row);
  };
  if (input.confirmOverlay?.requestId) {
    push({
      requestId: input.confirmOverlay.requestId,
      preview: input.confirmOverlay.preview,
      resolved: input.confirmOverlay.resolved,
    });
  }
  for (const block of input.blocks) {
    if (block.kind !== "confirm") continue;
    if (block.confirmKind === "work_record_post_turn") continue;
    push({
      requestId: block.requestId,
      preview: block.preview,
      confirmKind: block.confirmKind,
      resolved: block.resolved,
    });
  }
  return rows;
}

function liveWorkInstructions(input: CurrentWorkLiveSessionInput): CurrentWorkLiveWorkInstruction[] {
  return input.blocks
    .filter((block): block is Extract<typeof block, { kind: "work-instruction" }> =>
      block.kind === "work-instruction" && LIVE_WORK_INSTRUCTION_STATES.has(block.state))
    .map(block => ({
      text: block.text,
      state: block.state,
      deliveryStage: block.deliveryStage,
      reason: block.reason,
    }));
}

/** Collect queue rows that exist only in the live session (confirms / orchestration), not yet in work records. */
export function collectCurrentWorkLiveQueueEntries(input: CurrentWorkLiveSessionInput): CurrentWorkLiveQueueEntry[] {
  const entries: CurrentWorkLiveQueueEntry[] = [];
  const confirms = confirmBlocks(input);
  if (confirms.length) {
    entries.push({
      id: "live-confirm",
      title: confirms.length === 1 ? "主会话授权" : `主会话授权（${confirms.length} 项）`,
      status: "等待确认",
      attention: true,
      detailHtml: confirms.map((confirm, index) => {
        const heading = confirms.length === 1 ? "待你确认" : `待确认 ${index + 1}`;
        const kind = confirm.confirmKind === "project_worker_requirement_verification"
          ? "项目 worker 核验"
          : "工具 / 批次授权";
        return `<section class="unified-current-work-live-confirm"><h5>${heading} · ${kind}</h5><p class="cw-live-preview">${escapeHtml(truncatePreview(confirm.preview || "（预览为空）"))}</p><p class="cw-live-hint">授权卡片在对话中；点「回到对话」处理。</p></section>`;
      }).join(""),
    });
  }

  const instructions = liveWorkInstructions(input);
  for (const [index, instruction] of instructions.entries()) {
    const status = workInstructionChatStatusLabel(
      instruction.state,
      instruction.deliveryStage,
      instruction.reason,
    ) ?? "进行中";
    entries.push({
      id: `live-work-instruction-${index}`,
      title: "并行多项目编排",
      status,
      attention: instruction.state !== "queued",
      detailHtml: `<p class="cw-live-preview">${escapeHtml(truncatePreview(instruction.text || "（工作说明为空）"))}</p>`
        + `<p class="cw-live-hint">工作记录尚未写入概况；编排状态以对话与工作台同步为准。</p>`,
    });
  }

  if (!entries.length && input.turnActive && ["running", "queued", "stopping"].includes(input.executionState)) {
    entries.push({
      id: "live-execution",
      title: "当前回合",
      status: input.executionState === "running" ? "执行中" : "状态待确认",
      attention: true,
      detailHtml: `<p class="cw-live-hint">回合进行中，但 Structured 工作记录组尚未出现；请回到对话查看进度或授权。</p>`,
    });
  }

  return entries;
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

export function renderCurrentWorkLiveQueueHtml(input: CurrentWorkLiveSessionInput | null | undefined): string {
  if (!input) return "";
  const entries = collectCurrentWorkLiveQueueEntries(input);
  if (!entries.length) return "";
  return entries.map(entry => `<article class="unified-current-work-group cw-live-queue-entry" data-cw-live-entry="${escapeHtml(entry.id)}">`
    + `<h4>${escapeHtml(entry.title)}</h4>`
    + `<div class="unified-current-work-group-summary"><span class="unified-current-work-group-status">${escapeHtml(entry.status)}</span></div>`
    + `<div class="unified-current-work-items">${entry.detailHtml}</div>`
    + `</article>`).join("");
}

export function currentWorkLiveQueueCount(input: CurrentWorkLiveSessionInput | null | undefined): number {
  if (!input) return 0;
  return collectCurrentWorkLiveQueueEntries(input).length;
}
