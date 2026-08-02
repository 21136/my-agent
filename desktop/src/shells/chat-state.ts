import type { ServerEvent } from "../api/ws";

export type ChatBlock =
  | { kind: "user"; text: string; turnIndex: number }
  | { kind: "assistant"; text: string; turnIndex: number }
  | { kind: "assistant-streaming"; text: string; turnIndex: number; turnKey: string }
  | { kind: "notice"; text: string }
  | {
      kind: "process";
      lines: string[];
      reasoning: string;
      collapsed: boolean;
      turnKey: string;
      /** Phase 27 M0 — per-call running cards */
      tools?: Array<{
        callId: string;
        tool: string;
        summary: string;
        status: "running" | "ok" | "fail";
        startedAt: number;
        endedAt?: number;
        endSummary?: string;
        progressText?: string;
        logsTail?: string;
      }>;
    }
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
  beginTurnActivity(): void;
  resetTurnActivity(): void;
  isWorking(): boolean;
  pushUserMessage(text: string): number;
  toggleProcessCollapsed(turnKey: string): void;
  /** C3: optimistic resolve + only accept current requestId. Returns false if ignored. */
  submitConfirm(requestId: string, choice: "y" | "n" | "a"): boolean;
  /** Phase 15: debounce Stop and optimistically resolve a pending confirm. */
  requestCancel(): boolean;
  handleEvent(event: ServerEvent): void;
}

export function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function historyFromItems(
  items: Array<{ role: "user" | "assistant"; text: string }>,
): { blocks: ChatBlock[]; turnCounter: number; seeds: HistoryStarSeed[] } {
  let turnIndex = 0;
  const blocks: ChatBlock[] = [];
  const seeds: HistoryStarSeed[] = [];

  for (const item of items) {
    if (item.role === "user") {
      turnIndex += 1;
      blocks.push({ kind: "user", text: item.text, turnIndex });
      seeds.push({ role: "user", turnIndex });
    } else {
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

export function isConfirmInProgressLabel(label: string | undefined): boolean {
  if (!label) return false;
  return label.includes("执行中") || label === "提交中…";
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
    return (
      !model.confirmPending && (model.toolsRunning > 0 || (model.turnActive && !model.turnFinished))
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
    if (!model.turnActive && !model.confirmPending && model.toolsRunning === 0) {
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

  function beginTurn(): number {
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

  function ensureProcessBlock(): ChatBlock & { kind: "process" } {
    const existing = model.blocks.find(
      (b) => b.kind === "process" && b.turnKey === model.currentTurnKey && !b.collapsed,
    );
    if (existing?.kind === "process") {
      return existing;
    }
    const block: ChatBlock & { kind: "process" } = {
      kind: "process",
      lines: [],
      reasoning: "",
      collapsed: false,
      turnKey: model.currentTurnKey,
      tools: [],
    };
    model.blocks.push(block);
    return block;
  }

  function collapseCurrentProcess(): void {
    const block = model.blocks.find(
      (b) => b.kind === "process" && b.turnKey === model.currentTurnKey && !b.collapsed,
    );
    if (block?.kind === "process") {
      block.collapsed = true;
    }
  }

  function toggleProcessCollapsed(turnKey: string): void {
    const block = model.blocks.find((b) => b.kind === "process" && b.turnKey === turnKey);
    if (block?.kind === "process") {
      block.collapsed = !block.collapsed;
      notify();
    }
  }

  function handleEvent(event: ServerEvent): void {
    switch (event.type) {
      case "session.banner":
        resetTurnActivity();
        model.confirmPending = false;
        model.confirmSubmitting = false;
        model.cancelRequested = false;
        model.confirmOverlay = null;
        model.sessionId = event.session_id;
        hooks.onSessionBanner?.(event.session_id);
        notify();
        break;
      case "session.history": {
        resetTurnActivity();
        model.confirmPending = false;
        model.confirmSubmitting = false;
        model.cancelRequested = false;
        model.confirmOverlay = null;
        const loaded = historyFromItems(event.items);
        model.blocks = loaded.blocks;
        model.turnCounter = loaded.turnCounter;
        hooks.onHistoryLoaded?.(loaded);
        notify();
        break;
      }
      case "turn.start":
        beginTurnActivity();
        hooks.onTurnStart?.(event, currentTurnIndex());
        notify();
        break;
      case "turn.end":
        if (event.finish_reason === "cancelled" || event.finish_reason === "timeout") {
          model.blocks = model.blocks.filter(
            (block) =>
              !(
                block.kind === "assistant-streaming" &&
                block.turnKey === model.currentTurnKey
              ),
          );
          model.assistantBuffer = "";
          // insert a visible notice so the chat shows cancellation was processed
          const label = event.finish_reason === "timeout" ? "回合超时已停止" : "回合已停止";
          model.blocks.push({ kind: "notice", text: label });
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
          const proc = ensureProcessBlock();
          if (!proc.tools) proc.tools = [];
          proc.tools.push({
            callId: event.call_id,
            tool: event.tool,
            summary: event.summary || event.tool,
            status: "running",
            startedAt: Date.now(),
          });
          proc.lines.push(`· ${event.tool}  ${event.summary}`);
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
          const ms = now - started;
          const dur = ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
          const proc = ensureProcessBlock();
          proc.lines.push(`  ${event.ok ? "✓" : "✗"} ${event.tool} (${dur})`);
          if (!proc.tools) proc.tools = [];
          let card = proc.tools.find((t) => t.callId === event.call_id);
          if (!card) {
            card = {
              callId: event.call_id,
              tool: event.tool,
              summary: event.tool,
              status: "running",
              startedAt: started,
            };
            proc.tools.push(card);
          }
          card.status = event.ok ? "ok" : "fail";
          card.endedAt = now;
          card.endSummary = event.summary || (event.ok ? "完成" : "失败");
          if (typeof event.logs_tail === "string" && event.logs_tail.trim()) {
            card.logsTail = event.logs_tail.trim();
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
          const card = proc?.kind === "process"
            ? proc.tools?.find((t) => t.callId === event.call_id)
            : undefined;
          if (card && card.status === "running") {
            if (typeof event.text === "string" && event.text.trim()) {
              card.progressText = event.text.trim();
            } else if (typeof event.elapsed_sec === "number") {
              card.progressText = `仍在执行… ${event.elapsed_sec}s`;
            }
            notify();
          }
        }
        break;
      case "reasoning.delta":
        if (options.showProcess) {
          const proc = ensureProcessBlock();
          proc.reasoning += event.text;
        }
        notify();
        break;
      case "assistant.delta": {
        const streaming = ensureStreamingAssistant();
        streaming.text += event.text;
        model.assistantBuffer = streaming.text;
        notify();
        break;
      }
      case "assistant.done": {
        if (options.showProcess) {
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
        if (finalText) {
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
  }

  return {
    model,
    currentTurnIndex,
    beginTurn,
    beginTurnActivity,
    resetTurnActivity,
    isWorking,
    pushUserMessage,
    toggleProcessCollapsed,
    submitConfirm,
    requestCancel,
    handleEvent,
  };
}
