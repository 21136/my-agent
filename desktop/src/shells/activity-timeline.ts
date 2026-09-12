import {
  type ProcessActivityBlock,
  type ThinkEntry,
  type ToolEntry,
  ensureProcessEntries,
  formatThinkElapsedSec,
  getProcessTools,
  isThinkBodyOpen,
  thinkEntryTitle,
} from "./activity-state";
import { escapeHtml, formatToolElapsed } from "./chat-state";
import {
  formatReasoningDisplay,
  humanizeToolAction,
  humanizeToolSubline,
  resolveEffectiveToolName,
  shouldNeutralizeReasoningSummary,
} from "./unified/output-display";

const PROCESS_TOOL_LINES_CAP = 6;
/** Keep each think segment individually expandable; do not bundle into「思考 ×N」. */
const PINNED_THINK_FOLD_MIN = 99;

function truncateToolHint(text: string, max = 72): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  if (oneLine.length <= max) return oneLine;
  return `${oneLine.slice(0, max - 1)}…`;
}

function displayReasoning(text: string): string {
  return formatReasoningDisplay(text);
}

function isCompactWait(
  entry: ThinkEntry,
  opts: { liveTurn: boolean; waiting: boolean },
): boolean {
  if (!opts.liveTurn || entry.phase !== "streaming") return false;
  return opts.waiting || !entry.text.trim();
}

function renderThinkBody(
  entry: ThinkEntry,
  opts: { streaming: boolean; open: boolean; compactWait: boolean },
): string {
  if (opts.compactWait || !opts.open) return "";
  const displayText = displayReasoning(entry.text);
  if (opts.streaming && entry.text.trim()) {
    return `<div class="unified-thinking-body"><div class="unified-thinking-stream-text">${escapeHtml(displayText)}</div></div>`;
  }
  if (!entry.text.trim()) return "";
  return `<div class="unified-thinking-body">${escapeHtml(displayText)}</div>`;
}

function renderThinkEntry(
  entry: ThinkEntry,
  turnKey: string,
  opts: { liveTurn: boolean; waiting: boolean; waitStartedAt?: number },
): string {
  const streaming = opts.liveTurn && entry.phase === "streaming";
  const waiting = opts.waiting && !entry.text.trim();
  const compactWait = isCompactWait(entry, opts);
  const open = !compactWait && isThinkBodyOpen(entry);
  const title = thinkEntryTitle(entry, {
    waiting: waiting || compactWait,
    waitStartedAt: opts.waitStartedAt,
  });
  const phaseCls = streaming || waiting || compactWait ? " is-streaming" : " is-pinned";
  const openCls = open ? " is-open" : "";
  const waitCls = compactWait ? " is-waiting is-compact-wait" : waiting ? " is-waiting" : "";
  const compactPinned = entry.phase === "pinned" && !open && !waiting && !compactWait;
  const compactCls = compactPinned ? " is-compact" : "";
  const body = renderThinkBody(entry, { streaming, open, compactWait });
  const toggle = compactWait || waiting
    ? `<div class="unified-thinking-summary is-static" aria-live="polite">
        <span class="unified-thinking-pulse" aria-hidden="true"></span>
        <span class="unified-thinking-waiting-text">${escapeHtml(title)}</span>
      </div>`
    : `<button type="button" class="unified-thinking-summary" data-thinking-toggle="${escapeHtml(turnKey)}" data-think-id="${escapeHtml(entry.id)}" aria-expanded="${open ? "true" : "false"}">${escapeHtml(title)}</button>`;
  return `<div class="unified-activity-entry is-think unified-thinking${phaseCls}${openCls}${waitCls}${compactCls}" data-turn="${escapeHtml(turnKey)}" data-think-id="${escapeHtml(entry.id)}">
    ${toggle}
    ${body}
  </div>`;
}

function isFoldablePinnedThink(entry: ThinkEntry): boolean {
  return entry.phase === "pinned" && !entry.userOpen;
}

function renderPinnedThinkFold(
  entries: ThinkEntry[],
  turnKey: string,
  liveTurn: boolean,
): string {
  const body = entries
    .map((entry) => renderThinkEntry(entry, turnKey, { liveTurn, waiting: false }))
    .join("");
  return `<details class="unified-think-fold">
    <summary>思考 ×${entries.length}</summary>
    <div class="unified-think-fold-body">${body}</div>
  </details>`;
}

function renderToolEntry(tool: ToolEntry, now: number): string {
  const running = tool.status === "running";
  const elapsedMs = running ? now - tool.startedAt : (tool.endedAt ?? now) - tool.startedAt;
  const mark = running ? "·" : tool.status === "ok" ? "✓" : "✗";
  const elapsedLabel = running
    ? `运行中… ${formatToolElapsed(elapsedMs)}`
    : formatToolElapsed(elapsedMs);
  const progressBit =
    running && tool.progressText
      ? ` · ${escapeHtml(truncateToolHint(tool.progressText, 48))}`
      : "";
  const logsBit =
    !running && tool.status === "fail" && tool.logsTail
      ? `<details class="unified-tool-logs-inline"><summary>日志</summary><pre>${escapeHtml(tool.logsTail)}</pre></details>`
      : "";
  const label = humanizeToolAction(tool.tool, tool.summary, tool.endSummary);
  const sub = humanizeToolSubline(tool.status, tool.endSummary);
  const canonical = resolveEffectiveToolName(tool.tool, tool.summary);
  return `<div class="unified-activity-entry is-tool unified-process-line unified-tool-line is-${tool.status}" data-tool-call="${escapeHtml(tool.callId)}" data-started-at="${tool.startedAt}" data-status="${tool.status}" title="${escapeHtml(canonical)}">
    <span class="unified-tool-mark">${mark}</span>
    <span class="unified-tool-step">
      <span class="unified-tool-step-head">
        <span class="unified-tool-step-label">${escapeHtml(label)}</span>
        <button type="button" class="unified-tool-rename-btn" data-tool-rename="${escapeHtml(canonical)}" aria-label="改显示名称" title="自定义「${escapeHtml(canonical)}」的显示名称">改名</button>
      </span>
      ${sub ? `<span class="unified-tool-step-sub">${escapeHtml(sub)}</span>` : ""}
    </span>
    <span class="unified-tool-elapsed">${escapeHtml(elapsedLabel)}</span>${progressBit}${logsBit}
  </div>`;
}

function renderToolFold(
  hidden: ToolEntry[],
  visible: ToolEntry[],
  turnKey: string,
  foldOpen: boolean,
  now: number,
): string {
  const failHidden = hidden.filter((t) => t.status === "fail").length;
  const foldBit =
    hidden.length > 0
      ? `<details class="unified-tool-fold" data-tools-fold="${escapeHtml(turnKey)}"${foldOpen ? " open" : ""}>
          <summary>更早 ${hidden.length} 个工具${failHidden > 0 ? ` · ${failHidden} 失败` : ""}</summary>
          <div class="unified-tool-fold-body">${hidden.map((t) => renderToolEntry(t, now)).join("")}</div>
        </details>`
      : "";
  return `${foldBit}${visible.map((t) => renderToolEntry(t, now)).join("")}`;
}

export function renderActivityTimeline(
  block: ProcessActivityBlock,
  opts: { liveTurn: boolean; toolsFoldOpen: boolean },
): string {
  const entries = ensureProcessEntries(block);
  const hasPendingOnly =
    opts.liveTurn && Boolean(block.llmPending) && !entries.some((e) => e.kind === "think" && e.phase === "streaming");
  if (!entries.length && !hasPendingOnly) return "";

  const now = Date.now();
  const parts: string[] = [];
  let toolBuffer: ToolEntry[] = [];

  const flushTools = () => {
    if (!toolBuffer.length) return;
    const hiddenCount = Math.max(0, toolBuffer.length - PROCESS_TOOL_LINES_CAP);
    const hidden = hiddenCount > 0 ? toolBuffer.slice(0, hiddenCount) : [];
    const visible = hiddenCount > 0 ? toolBuffer.slice(hiddenCount) : toolBuffer;
    parts.push(
      `<div class="unified-tool-lines">${renderToolFold(hidden, visible, block.turnKey, opts.toolsFoldOpen, now)}</div>`,
    );
    toolBuffer = [];
  };

  if (hasPendingOnly) {
    const waitStartedAt = block.llmWaitStartedAt ?? now;
    const pendingThink: ThinkEntry = {
      kind: "think",
      id: "pending",
      text: "",
      phase: "streaming",
      startedAt: waitStartedAt,
    };
    parts.push(renderThinkEntry(pendingThink, block.turnKey, {
      liveTurn: true,
      waiting: true,
      waitStartedAt,
    }));
  }

  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    if (entry.kind === "think") {
      flushTools();
      if (isFoldablePinnedThink(entry)) {
        const batch: ThinkEntry[] = [];
        while (i < entries.length) {
          const candidate = entries[i];
          if (candidate.kind !== "think" || !isFoldablePinnedThink(candidate)) break;
          batch.push(candidate);
          i += 1;
        }
        i -= 1;
        if (batch.length >= PINNED_THINK_FOLD_MIN) {
          parts.push(renderPinnedThinkFold(batch, block.turnKey, opts.liveTurn));
        } else {
          for (const think of batch) {
            parts.push(renderThinkEntry(think, block.turnKey, { liveTurn: opts.liveTurn, waiting: false }));
          }
        }
      } else {
        parts.push(renderThinkEntry(entry, block.turnKey, { liveTurn: opts.liveTurn, waiting: false }));
      }
    } else {
      toolBuffer.push(entry);
    }
  }
  flushTools();

  if (!parts.length) return "";
  return `<div class="unified-activity-timeline">${parts.join("")}</div>`;
}

export function timelineHasThinking(block: ProcessActivityBlock, liveTurn: boolean): boolean {
  const entries = block.entries ?? [];
  if (liveTurn && block.llmPending) return true;
  return entries.some((e) => e.kind === "think" && (e.phase === "streaming" || e.text.trim()));
}

/** Update think title/body in-place for streaming ticks. */
export function syncThinkDom(
  el: HTMLElement,
  entry: ThinkEntry,
  opts: { waiting?: boolean; liveTurn?: boolean; waitStartedAt?: number },
): void {
  const compactWait =
    Boolean(opts.liveTurn) &&
    entry.phase === "streaming" &&
    (!entry.text.trim() || Boolean(opts.waiting));
  const title = thinkEntryTitle(entry, {
    waiting: compactWait || Boolean(opts.waiting),
    waitStartedAt: opts.waitStartedAt,
  });
  const summary = el.querySelector<HTMLElement>(".unified-thinking-summary, .unified-thinking-waiting-text");
  if (summary) summary.textContent = title;
  if (compactWait) {
    el.querySelector<HTMLElement>(".unified-thinking-body")?.remove();
    el.classList.add("is-compact-wait", "is-waiting");
    return;
  }
  const liveStreaming = Boolean(opts.liveTurn) && entry.phase === "streaming" && Boolean(entry.text.trim());
  let body = el.querySelector<HTMLElement>(".unified-thinking-body");
  if (liveStreaming) {
    if (!body) {
      body = document.createElement("div");
      body.className = "unified-thinking-body";
      el.appendChild(body);
    }
    let streamText = body.querySelector<HTMLElement>(".unified-thinking-stream-text");
    if (entry.text.trim()) {
      const displayText = formatReasoningDisplay(entry.text);
      if (!streamText) {
        streamText = document.createElement("div");
        streamText.className = "unified-thinking-stream-text";
        body.appendChild(streamText);
      }
      if (streamText.textContent !== displayText) {
        streamText.textContent = displayText;
      }
      streamText.scrollTop = streamText.scrollHeight;
    }
    body.scrollTop = body.scrollHeight;
    return;
  }
  if (body && entry.phase === "streaming" && entry.text) {
    const displayText = formatReasoningDisplay(entry.text);
    if (body.textContent !== displayText) {
      body.textContent = displayText;
    }
    body.scrollTop = body.scrollHeight;
  }
}

export { formatThinkElapsedSec, getProcessTools };
