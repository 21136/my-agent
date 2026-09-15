import type { ExecutionStateName, ServerEvent } from "../api/ws";
import {
  type ProcessActivityBlock,
  activityEntriesPrint,
  addToolStart,
  appendReasoning,
  clearLlmPending,
  ensureProcessEntries,
  finalizeProcessAfterTurn as finalizeActivityProcess,
  findToolEntry,
  isProcessThinkingLive,
  markLlmPending,
  openLastPinnedThink,
  pinOpenThink,
  prepareProcessForLlmRound as prepareActivityForLlmRound,
  syncProcessLegacyFields,
  toggleThinkOpen,
} from "./activity-state";

export type ChatBlock =
  | { kind: "user"; text: string; turnIndex: number }
  | { kind: "assistant"; text: string; turnIndex: number }
  | { kind: "assistant-streaming"; text: string; turnIndex: number; turnKey: string }
  | { kind: "plan-subagent"; status: "running" | "proposals_ready"; taskPreview?: string; summary?: string; proposalCount?: number; turnIndex: number }
  | { kind: "review-subagent"; status: "running" | "done"; taskPreview?: string; summary?: string; verdict?: string; blockersCount?: number; turnIndex: number }
  | { kind: "notice"; text: string }
  | (ProcessActivityBlock & { kind: "process" })
  | {
      kind: "confirm";
      requestId: string;
      preview: string;
      allowApproveAll: boolean;
      resolved?: string;
    };

export type ConfirmOverlay = {
  requestId: string;
  preview: string;
  allowApproveAll: boolean;
  resolved?: string;
};

export type HistoryStarSeed = {
  role: "user" | "assistant";
  turnIndex: number;
};

export interface ChatSessionModel {
  sessionId: string;
  confirmPending: boolean;
  confirmSubmitting: boolean;
  cancelRequested: boolean;
  confirmOverlay: ConfirmOverlay | null;
  currentTurnKey: string;
  turnCounter: number;
  blocks: ChatBlock[];
  assistantBuffer: string;
  turnActive: boolean;
  turnFinished: boolean;
  toolsRunning: number;
  executionRunId: string;
  executionState: ExecutionStateName;
  executionSequence: number;
  executionFinishReason: string | null;
  _toolTimers: Map<string, number>;
}

export interface ChatSessionOptions {
  /** grow: true · daily: false */
  showProcess: boolean;
  /** grow: inline block · daily: overlay only */
  confirmInBlocks: boolean;
}

export interface ChatSessionHooks {
  onChange?: () => void;
  onSessionBanner?: (sessionId: string) => void;
  onHistoryLoaded?: (payload: {
    blocks: ChatBlock[];
    turnCounter: number;
    seeds: HistoryStarSeed[];
  }) => void;
  onTurnStart?: (event: Extract<ServerEvent, { type: "turn.start" }>, turnIndex: number) => void;
  onLlmPending?: () => void;
  onToolStart?: (turnIndex: number) => void;
  onToolEnd?: (turnIndex: number) => void;
  onAssistantDone?: (turnIndex: number) => void;
  onConfirmRequest?: (turnIndex: number, overlay: ConfirmOverlay) => void;
  onConfirmDone?: (choice: string) => void;
  onTurnEnd?: (ok: boolean, finishReason: string) => void;
  onCheckerVerdict?: (event: { tool_name: string; verdict: string }) => void;
  onError?: () => void;
  /** Sidecar did not emit turn.end within watchdog window after Stop. */
  onCancelTimeout?: () => void;
}

const CANCEL_WATCHDOG_MS = 45_000;

export interface ChatSession {
  model: ChatSessionModel;
  currentTurnIndex(): number;
  beginTurn(): number;
  beginServerProcessTurn(): void;
  beginTurnActivity(): void;
  resetTurnActivity(): void;
  isWorking(): boolean;
  pushUserMessage(text: string): number;
  pushPlanSubagentCard(
    status: "running" | "proposals_ready",
    opts?: { taskPreview?: string; summary?: string; proposalCount?: number },
  ): number;
  updatePlanSubagentCard(
    status: "proposals_ready",
    opts?: { summary?: string; proposalCount?: number },
  ): void;
  toggleProcessCollapsed(turnKey: string): void;
  toggleThinkingOpen(turnKey: string, thinkId?: string): void;
  openThinking(turnKey: string): void;
  /** C3: optimistic resolve + only accept current requestId. Returns false if ignored. */
  submitConfirm(requestId: string, choice: "y" | "n" | "a"): boolean;
  /** Phase 15: debounce Stop and optimistically resolve a pending confirm. */
  requestCancel(): boolean;
  /** Returns false when a stale run event was ignored. */
  handleEvent(event: ServerEvent): boolean;
}

export function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function isEphemeralChatBlock(kind: ChatBlock["kind"]): boolean {
  return kind === "process" || kind === "assistant-streaming" || kind === "confirm";
}

/** UX-028 M2 — history reload must not resurrect live process/thinking UI. */
export function sanitizeChatBlocksForHistory(blocks: ChatBlock[]): ChatBlock[] {
  return blocks.filter((block) => !isEphemeralChatBlock(block.kind));
}

export function clearSessionEphemeralState(model: ChatSessionModel): void {
  model.currentTurnKey = "";
  model.assistantBuffer = "";
  model.blocks = sanitizeChatBlocksForHistory(model.blocks);
  model._toolTimers.clear();
}

export function isHarnessChatText(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith("[Harness]") || trimmed.startsWith("狂奔模式：") || trimmed.startsWith("狂奔模式:")) {
    return true;
  }
  return trimmed.startsWith("[狂奔续接]") || trimmed.slice(0, 40).includes("[狂奔续接]");
}

export function historyFromItems(
  items: Array<{ role: "user" | "assistant"; text: string }>,
): { blocks: ChatBlock[]; turnCounter: number; seeds: HistoryStarSeed[] } {
  let turnIndex = 0;
  const blocks: ChatBlock[] = [];
  const seeds: HistoryStarSeed[] = [];

  for (const item of items) {
    if (item.role === "user") {
      const text = item.text.trim();
      if (isHarnessChatText(text)) {
        continue;
      }
      turnIndex += 1;
      blocks.push({ kind: "user", text: item.text, turnIndex });
      seeds.push({ role: "user", turnIndex });
    } else {
      const text = item.text.trim();
      if (isHarnessChatText(text)) {
        continue;
      }
      const assistantTurn = turnIndex || 1;
      blocks.push({ kind: "assistant", text: item.text, turnIndex: assistantTurn });
      seeds.push({ role: "assistant", turnIndex: assistantTurn });
    }
  }

  return { blocks, turnCounter: turnIndex, seeds };
}

export function turnEndStatusText(finishReason: string): string | null {
  if (finishReason === "cancelled") return "已停止";
  if (finishReason === "timeout") return "已超时";
  if (finishReason === "task_paused") return "本项已完成";
  return null;
}

export function checkerVerdictStatusText(verdict: string): string {
  if (verdict === "pass") return "验收：通过";
  if (verdict === "warn") return "验收：警告";
  return "验收：失败";
}

/** Phase 27 M0 — RunningCard elapsed label. */
export function formatToolElapsed(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m${String(s).padStart(2, "0")}s`;
}

/** UX-021 — thinking accordion title elapsed label. */
export function formatThinkingElapsedSec(block: {
  reasoningPhase?: "idle" | "streaming" | "pinned";
  reasoningStartedAt?: number;
  reasoningPinnedAt?: number;
}): number {
  const start = block.reasoningStartedAt;
  if (!start) return 0;
  const end =
    block.reasoningPhase === "streaming"
      ? Date.now()
      : (block.reasoningPinnedAt ?? Date.now());
  return Math.max(1, Math.round((end - start) / 1000));
}

export function thinkingTitleLabel(block: {
  reasoning: string;
  llmPending?: boolean;
  reasoningPhase?: "idle" | "streaming" | "pinned";
  reasoningStartedAt?: number;
  reasoningPinnedAt?: number;
}): string {
  if (!block.reasoning.trim()) {
    if (block.llmPending) {
      const sec = formatThinkingElapsedSec({ ...block, reasoningPhase: "streaming" });
      return sec > 0 ? `思考中…（${sec}s）` : "思考中…";
    }
    return "";
  }
  if (block.reasoningPhase === "streaming") {
    const sec = formatThinkingElapsedSec(block);
    return sec > 0 ? `思考中…（${sec}s）` : "思考中…";
  }
  const sec = formatThinkingElapsedSec(block);
  return `思考 · ${sec}s`;
}

export function isThinkingBodyOpen(block: {
  reasoning: string;
  llmPending?: boolean;
  reasoningPhase?: "idle" | "streaming" | "pinned";
  reasoningUserOpen?: boolean;
}): boolean {
  if (block.llmPending && !block.reasoning.trim()) return true;
  if (!block.reasoning.trim()) return false;
  if (block.reasoningPhase === "streaming") return true;
  return Boolean(block.reasoningUserOpen);
}

export function isConfirmInProgressLabel(label: string | undefined): boolean {
  if (!label) return false;
  return label.includes("执行中") || label === "提交中…";
}

function normalizedRunId(runId: string | undefined): string {
  return typeof runId === "string" ? runId.trim() : "";
}

const CONFIRM_LABELS: Record<string, string> = {
  y: "已同意，执行中…",
  n: "已跳过",
  a: "本会话均允许 · 执行中…",
  timeout: "确认超时",
  stale: "已过期",
  cancelled: "已取消",
};

export function createChatSession(
  options: ChatSessionOptions,
  hooks: ChatSessionHooks = {},
): ChatSession {
  const model: ChatSessionModel = {
    sessionId: "",
    confirmPending: false,
    confirmSubmitting: false,
    cancelRequested: false,
    confirmOverlay: null,
    currentTurnKey: "",
    turnCounter: 0,
    blocks: [],
    assistantBuffer: "",
    turnActive: false,
    turnFinished: false,
    toolsRunning: 0,
    executionRunId: "",
    executionState: "idle",
    executionSequence: 0,
    executionFinishReason: null,
    _toolTimers: new Map<string, number>(),
  };

  let cancelWatchdog: ReturnType<typeof setTimeout> | null = null;

  function notify(): void {
    hooks.onChange?.();
  }

  function clearCancelWatchdog(): void {
    if (cancelWatchdog !== null) {
      clearTimeout(cancelWatchdog);
      cancelWatchdog = null;
    }
  }

  function currentTurnIndex(): number {
    return model.turnCounter;
  }

  function beginTurnActivity(): void {
    model.turnActive = true;
    model.turnFinished = false;
  }

  function findProcessBlock(turnKey: string): (ChatBlock & { kind: "process" }) | undefined {
    for (let i = model.blocks.length - 1; i >= 0; i--) {
      const block = model.blocks[i];
      if (block.kind === "process" && block.turnKey === turnKey) {
        return block;
      }
    }
    return undefined;
  }

  /** One process block per user turn — reopen collapsed instead of spawning another. */
  function ensureActiveProcessBlock(): ChatBlock & { kind: "process" } {
    const uncollapsed = model.blocks.find(
      (b) => b.kind === "process" && b.turnKey === model.currentTurnKey && !b.collapsed,
    );
    if (uncollapsed?.kind === "process") return uncollapsed;

    const collapsed = findProcessBlock(model.currentTurnKey);
    if (collapsed) {
      collapsed.collapsed = false;
      return collapsed;
    }

    const block: ChatBlock & { kind: "process" } = {
      kind: "process",
      lines: [],
      reasoning: "",
      collapsed: false,
      turnKey: model.currentTurnKey,
      reasoningPhase: "idle",
      reasoningUserOpen: false,
      tools: [],
      entries: [],
    };
    model.blocks.push(block);
    return block;
  }

  function prepareProcessForLlmRound(): ChatBlock & { kind: "process" } {
    const proc = ensureActiveProcessBlock();
    prepareActivityForLlmRound(proc);
    return proc;
  }

  function resetTurnActivity(): void {
    clearCancelWatchdog();
    model.turnActive = false;
    model.turnFinished = true;
    model.toolsRunning = 0;
    model.confirmSubmitting = false;
    model.cancelRequested = false;
  }

  function isWorking(): boolean {
    if (model.cancelRequested) return true;
    // C5: post-click submitting counts as working even while confirmPending clears.
    if (model.confirmSubmitting) return true;
    if (["queued", "running", "stopping"].includes(model.executionState)) return true;
    const liveThinking = model.blocks.some(
      (block) =>
        block.kind === "process" && isProcessThinkingLive(block, model.currentTurnKey),
    );
    return (
      !model.confirmPending &&
      (model.toolsRunning > 0 || (model.turnActive && !model.turnFinished) || liveThinking)
    );
  }

  function markConfirmResolved(requestId: string, label: string): void {
    if (options.confirmInBlocks) {
      const block = model.blocks.find((b) => b.kind === "confirm" && b.requestId === requestId);
      if (block?.kind === "confirm") {
        block.resolved = label;
      }
    }
    if (model.confirmOverlay?.requestId === requestId) {
      model.confirmOverlay = { ...model.confirmOverlay, resolved: label };
    }
  }

  function expirePreviousConfirms(exceptRequestId: string): void {
    if (!options.confirmInBlocks) return;
    for (const block of model.blocks) {
      if (block.kind === "confirm" && !block.resolved && block.requestId !== exceptRequestId) {
        block.resolved = "已过期";
      }
    }
  }

  function finalizeInProgressConfirms(terminalLabel: string): void {
    if (options.confirmInBlocks) {
      for (const block of model.blocks) {
        if (block.kind === "confirm" && isConfirmInProgressLabel(block.resolved)) {
          block.resolved = terminalLabel;
        }
      }
    }
    if (isConfirmInProgressLabel(model.confirmOverlay?.resolved)) {
      model.confirmOverlay = {
        ...model.confirmOverlay!,
        resolved: terminalLabel,
      };
    }
  }

  function submitConfirm(requestId: string, choice: "y" | "n" | "a"): boolean {
    const currentId = model.confirmOverlay?.requestId;
    if (!currentId || requestId !== currentId) {
      return false;
    }
    if (model.confirmOverlay?.resolved) {
      return false;
    }
    const pendingLabel = choice === "n" ? "提交拒绝…" : "提交中…";
    markConfirmResolved(requestId, pendingLabel);
    model.confirmSubmitting = true;
    beginTurnActivity();
    notify();
    return true;
  }

  function requestCancel(): boolean {
    if (model.cancelRequested) return false;
    if (
      !model.turnActive
      && !model.confirmPending
      && model.toolsRunning === 0
      && !["queued", "running", "stopping"].includes(model.executionState)
    ) {
      return false;
    }
    model.cancelRequested = true;
    model.confirmSubmitting = false;
    if (model.confirmOverlay?.requestId) {
      markConfirmResolved(model.confirmOverlay.requestId, "取消中…");
    }
    clearCancelWatchdog();
    cancelWatchdog = setTimeout(() => {
      if (!model.cancelRequested) return;
      model.blocks.push({
        kind: "notice",
        text: "停止请求超时，界面已恢复；若仍无响应请托盘退出后重启。",
      });
      resetTurnActivity();
      model.confirmPending = false;
      hooks.onCancelTimeout?.();
      notify();
    }, CANCEL_WATCHDOG_MS);
    notify();
    return true;
  }

  /** Close live thinking on prior turns — prevents stacked "思考中" when 狂奔 auto-sends. */
  function finalizeInFlightProcessBlocks(): void {
    for (const block of model.blocks) {
      if (block.kind !== "process") continue;
      const live =
        Boolean(block.llmPending) || block.reasoningPhase === "streaming";
      if (!live) continue;
      finalizeProcessAfterTurn(block);
      block.collapsed = true;
    }
  }

  /** New process timeline for each server turn / v2 chain segment (no user bubble). */
  function beginServerProcessTurn(): void {
    finalizeInFlightProcessBlocks();
    model.currentTurnKey = `turn-${Date.now()}`;
    model.assistantBuffer = "";
    beginTurnActivity();
  }

  function beginTurn(): number {
    finalizeInFlightProcessBlocks();
    model.currentTurnKey = `turn-${Date.now()}`;
    model.turnCounter += 1;
    model.assistantBuffer = "";
    beginTurnActivity();
    return model.turnCounter;
  }

  function pushUserMessage(text: string): number {
    const turnIndex = beginTurn();
    model.blocks.push({ kind: "user", text, turnIndex });
    notify();
    return turnIndex;
  }

  function pushPlanSubagentCard(
    status: "running" | "proposals_ready",
    opts?: { taskPreview?: string; summary?: string; proposalCount?: number },
  ): number {
    const turnIndex = beginTurn();
    model.blocks.push({
      kind: "plan-subagent",
      status,
      taskPreview: opts?.taskPreview,
      summary: opts?.summary,
      proposalCount: opts?.proposalCount,
      turnIndex,
    });
    notify();
    return turnIndex;
  }

  function updatePlanSubagentCard(
    status: "running" | "proposals_ready",
    opts?: { summary?: string; proposalCount?: number },
  ): void {
    for (let i = model.blocks.length - 1; i >= 0; i--) {
      const block = model.blocks[i];
      if (block.kind === "plan-subagent" && block.status === "running") {
        model.blocks[i] = {
          ...block,
          status,
          summary: opts?.summary ?? block.summary,
          proposalCount: opts?.proposalCount ?? block.proposalCount,
        };
        notify();
        return;
      }
    }
    pushPlanSubagentCard(status, opts);
  }

  function pushReviewSubagentCard(
    status: "running" | "done",
    opts?: {
      taskPreview?: string;
      summary?: string;
      verdict?: string;
      blockersCount?: number;
    },
  ): number {
    const turnIndex = beginTurn();
    model.blocks.push({
      kind: "review-subagent",
      status,
      taskPreview: opts?.taskPreview,
      summary: opts?.summary,
      verdict: opts?.verdict,
      blockersCount: opts?.blockersCount,
      turnIndex,
    });
    notify();
    return turnIndex;
  }

  function updateReviewSubagentCard(
    status: "done",
    opts?: { summary?: string; verdict?: string; blockersCount?: number },
  ): void {
    for (let i = model.blocks.length - 1; i >= 0; i--) {
      const block = model.blocks[i];
      if (block.kind === "review-subagent" && block.status === "running") {
        model.blocks[i] = {
          ...block,
          status,
          summary: opts?.summary ?? block.summary,
          verdict: opts?.verdict ?? block.verdict,
          blockersCount: opts?.blockersCount ?? block.blockersCount,
        };
        notify();
        return;
      }
    }
    pushReviewSubagentCard(status, opts);
  }

  function ensureStreamingAssistant(): ChatBlock & { kind: "assistant-streaming" } {
    const turnIndex = currentTurnIndex();
    const last = model.blocks[model.blocks.length - 1];
    if (
      last?.kind === "assistant-streaming" &&
      last.turnKey === model.currentTurnKey &&
      last.turnIndex === turnIndex
    ) {
      return last;
    }
    const block: ChatBlock & { kind: "assistant-streaming" } = {
      kind: "assistant-streaming",
      text: "",
      turnIndex,
      turnKey: model.currentTurnKey,
    };
    model.blocks.push(block);
    return block;
  }

  function collapseCurrentProcess(): void {
    const block = findProcessBlock(model.currentTurnKey);
    if (block && !block.collapsed) {
      block.collapsed = true;
    }
  }

  function finalizeProcessAfterTurn(proc: ChatBlock & { kind: "process" }): void {
    finalizeActivityProcess(proc);
  }

  function toggleProcessCollapsed(turnKey: string): void {
    const block = findProcessBlock(turnKey);
    if (block) {
      if (isProcessThinkingLive(block, model.currentTurnKey)) {
        block.collapsed = false;
      } else {
        block.collapsed = !block.collapsed;
      }
      notify();
    }
  }

  function toggleThinkingOpen(turnKey: string, thinkId?: string): void {
    const block = findProcessBlock(turnKey);
    if (!block) return;
    toggleThinkOpen(block, thinkId);
    notify();
  }

  function openThinking(turnKey: string): void {
    const block = findProcessBlock(turnKey);
    if (!block) return;
    openLastPinnedThink(block);
    notify();
  }

  function isStaleRunEvent(event: ServerEvent): boolean {
    const currentRunId = normalizedRunId(model.executionRunId);
    const eventRunId = [
      "execution.state",
      "turn.start",
      "turn.end",
      "turn.notice",
      "llm.pending",
      "assistant.delta",
      "assistant.done",
      "reasoning.delta",
      "activity.update",
      "confirm.request",
      "confirm.done",
      "tool.start",
      "tool.end",
      "tool.progress",
    ].includes(event.type)
      ? normalizedRunId("run_id" in event ? event.run_id : undefined)
      : "";
    if (!currentRunId || !eventRunId) return false;
    if (event.type === "execution.state") {
      return event.sequence < model.executionSequence
        || (event.sequence === model.executionSequence && eventRunId !== currentRunId);
    }
    return eventRunId !== currentRunId;
  }

  function handleEvent(event: ServerEvent): boolean {
    if (isStaleRunEvent(event)) return false;
    switch (event.type) {
      case "session.banner": {
        const sessionChanged = Boolean(model.sessionId) && model.sessionId !== event.session_id;
        resetTurnActivity();
        model.confirmPending = false;
        model.confirmSubmitting = false;
        model.cancelRequested = false;
        model.executionRunId = "";
        model.executionState = "idle";
        model.executionSequence = 0;
        model.executionFinishReason = null;
        model.confirmOverlay = null;
        if (sessionChanged) {
          clearSessionEphemeralState(model);
        }
        model.sessionId = event.session_id;
        hooks.onSessionBanner?.(event.session_id);
        notify();
        break;
      }
      case "session.history": {
        resetTurnActivity();
        model.confirmPending = false;
        model.confirmSubmitting = false;
        model.cancelRequested = false;
        model.executionRunId = "";
        model.executionState = "idle";
        model.executionSequence = 0;
        model.executionFinishReason = null;
        model.confirmOverlay = null;
        const loaded = historyFromItems(event.items);
        model.blocks = sanitizeChatBlocksForHistory(loaded.blocks);
        model.turnCounter = loaded.turnCounter;
        model.currentTurnKey = "";
        model.assistantBuffer = "";
        model._toolTimers.clear();
        if (event.truncated && (event.omitted_count ?? 0) > 0) {
          model.blocks.unshift({
            kind: "notice",
            text: `仅显示最近 ${event.items.length} 条对话（另有 ${event.omitted_count} 条未加载）。`,
          });
        }
        hooks.onHistoryLoaded?.(loaded);
        notify();
        break;
      }
      case "execution.state": {
        if (event.sequence < model.executionSequence) return false;
        const runId = normalizedRunId(event.run_id);
        if (runId) model.executionRunId = runId;
        model.executionState = event.state;
        model.executionSequence = event.sequence;
        model.executionFinishReason = event.finish_reason ?? null;
        if (event.state === "queued" || event.state === "running") {
          beginTurnActivity();
          model.cancelRequested = false;
        } else if (event.state === "stopping") {
          beginTurnActivity();
          model.cancelRequested = true;
        } else {
          model.turnActive = false;
          model.turnFinished = true;
          model.cancelRequested = false;
          model.toolsRunning = 0;
          model.confirmSubmitting = false;
        }
        notify();
        break;
      }
      case "turn.start": {
        const runId = normalizedRunId(event.run_id);
        if (runId) model.executionRunId = runId;
        beginServerProcessTurn();
        if (options.showProcess) {
          markLlmPending(prepareProcessForLlmRound());
        }
        hooks.onTurnStart?.(event, currentTurnIndex());
        notify();
        break;
      }
      case "llm.pending":
        if (options.showProcess) {
          markLlmPending(prepareProcessForLlmRound());
        }
        hooks.onLlmPending?.();
        notify();
        break;
      case "turn.end":
        if (event.run_id && !model.executionRunId) {
          const runId = normalizedRunId(event.run_id);
          if (runId) model.executionRunId = runId;
        }
        model.executionFinishReason = event.finish_reason;
        if (event.finish_reason === "cancelled" || event.finish_reason === "timeout") {
          model.blocks = model.blocks.filter(
            (block) =>
              !(
                block.kind === "assistant-streaming" &&
                block.turnKey === model.currentTurnKey
              ),
          );
          model.assistantBuffer = "";
          const hadLlmTimeoutNotice = model.blocks.some(
            (block) =>
              block.kind === "notice" &&
              typeof block.text === "string" &&
              block.text.includes("LLM 请求超时"),
          );
          const label =
            event.finish_reason === "timeout"
              ? hadLlmTimeoutNotice
                ? "LLM 请求超时已停止"
                : "回合超时已停止"
              : "回合已停止";
          model.blocks.push({ kind: "notice", text: label });
        }
        if (options.showProcess) {
          const proc = findProcessBlock(model.currentTurnKey);
          if (proc) {
            finalizeProcessAfterTurn(proc);
          }
          collapseCurrentProcess();
        }
        finalizeInProgressConfirms(event.finish_reason === "cancelled" ? "已取消" : "已完成执行");
        resetTurnActivity();
        model._toolTimers.clear();
        model.confirmPending = false;
        model.confirmSubmitting = false;
        hooks.onTurnEnd?.(event.ok, event.finish_reason);
        notify();
        break;
      case "turn.notice":
        model.blocks.push({ kind: "notice", text: event.text });
        notify();
        break;
      case "checker.verdict":
        model.blocks.push({
          kind: "notice",
          text: checkerVerdictStatusText(event.verdict),
        });
        hooks.onCheckerVerdict?.(event);
        notify();
        break;
      case "tool.start":
        model.toolsRunning += 1;
        model._toolTimers.set(event.call_id, Date.now());
        if (options.showProcess) {
          const proc = ensureActiveProcessBlock();
          addToolStart(proc, {
            callId: event.call_id,
            tool: event.tool,
            summary: event.summary || event.tool,
            status: "running",
            startedAt: Date.now(),
          });
        }
        hooks.onToolStart?.(currentTurnIndex());
        notify();
        break;
      case "tool.end":
        model.toolsRunning = Math.max(0, model.toolsRunning - 1);
        if (options.showProcess) {
          const startTime = model._toolTimers.get(event.call_id);
          const now = Date.now();
          const started = startTime ?? now;
          const proc = ensureActiveProcessBlock();
          let card = findToolEntry(proc, event.call_id);
          if (!card) {
            addToolStart(proc, {
              callId: event.call_id,
              tool: event.tool,
              summary: event.tool,
              status: "running",
              startedAt: started,
            });
            card = findToolEntry(proc, event.call_id);
          }
          if (card) {
            card.status = event.ok ? "ok" : "fail";
            card.endedAt = now;
            card.endSummary = event.summary || (event.ok ? "完成" : "失败");
            if (typeof event.logs_tail === "string" && event.logs_tail.trim()) {
              card.logsTail = event.logs_tail.trim();
            }
            syncProcessLegacyFields(proc);
          }
          model._toolTimers.delete(event.call_id);
        }
        if (model.turnFinished && model.toolsRunning === 0) {
          model.turnActive = false;
        }
        // When all tools done after an approve, flip confirm label to terminal state.
        if (model.toolsRunning === 0) {
          finalizeInProgressConfirms("已完成执行");
        }
        hooks.onToolEnd?.(currentTurnIndex());
        notify();
        break;
      case "tool.progress":
        if (options.showProcess) {
          const proc = model.blocks.find(
            (b) => b.kind === "process" && b.turnKey === model.currentTurnKey && !b.collapsed,
          );
          const card =
            proc?.kind === "process" ? findToolEntry(proc, event.call_id) : undefined;
          if (card && card.status === "running") {
            if (typeof event.text === "string" && event.text.trim()) {
              card.progressText = event.text.trim();
            } else if (typeof event.elapsed_sec === "number") {
              card.progressText = `仍在执行… ${event.elapsed_sec}s`;
            }
            syncProcessLegacyFields(proc!);
            notify();
          }
        }
        break;
      case "reasoning.delta":
        if (options.showProcess) {
          const proc = ensureActiveProcessBlock();
          clearLlmPending(proc);
          appendReasoning(proc, event.text);
        }
        notify();
        break;
      case "assistant.delta": {
        if (options.showProcess) {
          const proc = model.blocks.find(
            (b) => b.kind === "process" && b.turnKey === model.currentTurnKey && !b.collapsed,
          );
          if (proc?.kind === "process") {
            pinOpenThink(proc);
            clearLlmPending(proc);
          }
        }
        const streaming = ensureStreamingAssistant();
        streaming.text += event.text;
        if (isHarnessChatText(streaming.text)) {
          const idx = model.blocks.lastIndexOf(streaming);
          if (idx >= 0) model.blocks.splice(idx, 1);
          model.assistantBuffer = "";
        } else {
          model.assistantBuffer = streaming.text;
        }
        notify();
        break;
      }
      case "assistant.done": {
        if (options.showProcess) {
          const proc = findProcessBlock(model.currentTurnKey);
          if (proc) {
            finalizeProcessAfterTurn(proc);
          }
          collapseCurrentProcess();
        }
        const finalText = event.text.trim();
        const turnIndex = currentTurnIndex();
        // Drop every streaming bubble for this turn (key mismatch used to leave orphans).
        for (let i = model.blocks.length - 1; i >= 0; i--) {
          const b = model.blocks[i];
          if (b.kind === "assistant-streaming" && b.turnIndex === turnIndex) {
            model.blocks.splice(i, 1);
          }
        }
        if (finalText && !isHarnessChatText(finalText)) {
          const last = model.blocks[model.blocks.length - 1];
          // Idempotent: duplicate assistant.done must not create a second identical bubble.
          const dup =
            last?.kind === "assistant" &&
            last.turnIndex === turnIndex &&
            last.text === finalText;
          if (!dup) {
            model.blocks.push({ kind: "assistant", text: finalText, turnIndex });
          }
        }
        model.assistantBuffer = "";
        model.turnFinished = true;
        if (model.toolsRunning === 0) {
          model.turnActive = false;
        }
        hooks.onAssistantDone?.(turnIndex);
        notify();
        break;
      }
      case "notice":
        model.blocks.push({ kind: "notice", text: event.text });
        notify();
        break;
      case "error":
        model.blocks.push({ kind: "notice", text: event.message });
        resetTurnActivity();
        hooks.onError?.();
        notify();
        break;
      case "confirm.request": {
        model.confirmPending = true;
        model.confirmSubmitting = false;
        expirePreviousConfirms(event.request_id);
        const overlay: ConfirmOverlay = {
          requestId: event.request_id,
          preview: event.preview,
          allowApproveAll: event.allow_approve_all,
        };
        model.confirmOverlay = overlay;
        if (options.confirmInBlocks) {
          model.blocks.push({
            kind: "confirm",
            requestId: overlay.requestId,
            preview: overlay.preview,
            allowApproveAll: overlay.allowApproveAll,
          });
        }
        hooks.onConfirmRequest?.(currentTurnIndex(), overlay);
        notify();
        break;
      }
      case "confirm.done": {
        const isCurrent = model.confirmOverlay?.requestId === event.request_id;
        const resolved = CONFIRM_LABELS[event.choice] ?? event.choice;
        markConfirmResolved(event.request_id, resolved);
        if (!isCurrent) {
          notify();
          break;
        }
        model.confirmPending = false;
        model.confirmSubmitting = false;
        const rejected =
          event.choice === "n" ||
          event.choice === "timeout" ||
          event.choice === "stale" ||
          event.choice === "cancelled";
        if (!rejected) {
          beginTurnActivity();
        }
        hooks.onConfirmDone?.(event.choice);
        notify();
        break;
      }
      case "prompt.request":
        model.blocks.push({ kind: "notice", text: event.prompt });
        notify();
        break;
      default:
        break;
    }
    return true;
  }

  return {
    model,
    currentTurnIndex,
    beginTurn,
    beginServerProcessTurn,
    beginTurnActivity,
    resetTurnActivity,
    isWorking,
    pushUserMessage,
    pushPlanSubagentCard,
    updatePlanSubagentCard,
    pushReviewSubagentCard,
    updateReviewSubagentCard,
    toggleProcessCollapsed,
    toggleThinkingOpen,
    openThinking,
    submitConfirm,
    requestCancel,
    handleEvent,
  };
}
