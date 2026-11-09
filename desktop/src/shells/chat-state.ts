import type { ExecutionStateName, ServerEvent } from "../api/ws";
import { parseProjectWorkerVerification, parseWorkRecordAutoCandidate } from "./unified/confirm-preview";
import { settledToolStatus } from "./tool-outcome";
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

export type UserMessagePersistenceState = "pending" | "saved" | "unknown";
export type UserMessagePersistence = {
  requestId: string;
  sessionId: string | null;
  state: UserMessagePersistenceState;
  receiptEligible: boolean;
  /** Saved refers to the user message row only; attached-file retention remains unknown. */
  attachmentRetention: "unknown" | "not_applicable";
};

export type ChatBlock =
  | { kind: "user"; text: string; turnIndex: number; persistence?: UserMessagePersistence }
  | { kind: "assistant"; text: string; turnIndex: number }
  | { kind: "assistant-streaming"; text: string; turnIndex: number; turnKey: string }
  | { kind: "work-instruction"; requestId: string; sessionId: string; workRunId: string; text: string; state: "sending" | "queued" | "orchestration_dispatching" | "orchestration_active" | "delivered" | "undelivered"; reason?: string; deliveryStage?: "next_model_request_context" }
  | { kind: "parallel-chat"; requestId: string; sessionId: string; workRunId: string; promptText: string; responseText: string; deliveryState: "pending" | "delivered" | "done" | "error"; cancelRequested?: boolean; errorCode?: string; errorMessage?: string }
  | ToolPromptBlock
  | { kind: "review-subagent"; status: "running" | "done"; taskPreview?: string; summary?: string; verdict?: string; blockersCount?: number; turnIndex: number }
  | { kind: "notice"; text: string; plainText?: boolean }
  | { kind: "work-card-reference"; presentationId: string; cardId: string; text: string }
  | { kind: "generated-image"; agentPath: string; turnIndex: number }
  | { kind: "generated-video"; agentPath: string; turnIndex: number }
  | { kind: "inline-file"; agentPath: string; turnIndex: number }
  | (ProcessActivityBlock & { kind: "process" })
  | {
      kind: "confirm";
      confirmKind?: "work_record_post_turn" | "project_worker_requirement_verification";
      confirmSubtype?: "work_record_reply_excerpt" | "work_record_auto_candidate" | "project_worker_requirement_verification";
      workRecordCandidate?: unknown;
      projectWorkerVerification?: unknown;
      sessionId?: string;
      requestId: string;
      preview: string;
      allowApproveAll: boolean;
      resolved?: string;
    };

export type ToolPromptBlock = {
  kind: "tool-prompt";
  requestId: string;
  runId: string;
  sessionId: string;
  prompt: string;
  responseText: string;
  state: "waiting" | "sending" | "accepted" | "rejected";
  reason?: string;
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
  pendingToolPrompt: ToolPromptBlock | null;
  currentTurnKey: string;
  turnCounter: number;
  blocks: ChatBlock[];
  assistantBuffer: string;
  turnActive: boolean;
  turnFinished: boolean;
  /** Local monotonic clock for status elapsed while waiting on server/LLM/tools. */
  turnActivityStartedAt: number;
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
}

const CANCEL_WATCHDOG_MS = 45_000;
const USER_MESSAGE_REQUEST_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The current renderer protocol omits `diff`; validate the server extension at its boundary. */
function hasStringDiff(
  event: Extract<ServerEvent, { type: "tool.end" }>,
): event is Extract<ServerEvent, { type: "tool.end" }> & { diff?: string } {
  return !("diff" in event) || typeof event.diff === "string";
}

function normalizeDisplayPaths(paths: unknown): string[] {
  if (!Array.isArray(paths)) return [];
  return paths
    .filter((path): path is string => typeof path === "string" && path.trim().length > 0)
    .map((path) => path.trim().replace(/\\/g, "/"));
}

export interface ChatSession {
  model: ChatSessionModel;
  currentTurnIndex(): number;
  beginTurn(): number;
  beginServerProcessTurn(): void;
  beginTurnActivity(): void;
  resetTurnActivity(): void;
  isWorking(): boolean;
  pushUserMessage(text: string, persistence?: { requestId: string; sessionId: string | null; receiptEligible: boolean; hasAttachments?: boolean }): number;
  beginToolPromptResponse(requestId: string, runId: string, sessionId: string, text: string): boolean;
  addParallelChatRequest(text: string, requestId: string, workRunId: string): boolean;
  addWorkInstructionRequest(text: string, requestId: string, sessionId: string, workRunId: string): boolean;
  requestParallelChatCancel(requestId: string): boolean;
  pushReviewSubagentCard(
    status: "running" | "done",
    opts?: { taskPreview?: string; summary?: string; verdict?: string; blockersCount?: number },
  ): number;
  updateReviewSubagentCard(
    status: "done",
    opts?: { summary?: string; verdict?: string; blockersCount?: number },
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
  return kind === "process" || kind === "assistant-streaming" || kind === "confirm" || kind === "parallel-chat" || kind === "work-instruction" || kind === "tool-prompt";
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

export type SessionHistoryItem =
  | {
      role: "user" | "assistant";
      text: string;
      /** Display refs to rehydrate after session.history (CHAT-IMAGE-DISPLAY). */
      inline_images?: string[];
      inline_videos?: string[];
      inline_files?: string[];
    }
  | {
      role: "work_card";
      type: "work_card_reference";
      presentation_id: string;
      card_id: string;
      text: string;
    };

const WORK_CARD_PRESENTATION_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const WORK_CARD_IDENTITY = /^wc-[0-9a-f]{64}$/;

export function historyFromItems(
  items: SessionHistoryItem[],
): { blocks: ChatBlock[]; turnCounter: number; seeds: HistoryStarSeed[] } {
  let turnIndex = 0;
  const blocks: ChatBlock[] = [];
  const seeds: HistoryStarSeed[] = [];

  for (const item of items) {
    if (item.role === "work_card") {
      const validReference = item.type === "work_card_reference"
        && typeof item.presentation_id === "string" && WORK_CARD_PRESENTATION_UUID.test(item.presentation_id)
        && typeof item.card_id === "string" && WORK_CARD_IDENTITY.test(item.card_id)
        && typeof item.text === "string";
      blocks.push({
        kind: "work-card-reference",
        presentationId: typeof item.presentation_id === "string" ? item.presentation_id : "",
        cardId: typeof item.card_id === "string" ? item.card_id : "",
        text: validReference ? item.text : "历史工作卡片引用无效，无法安全读取卡片。",
      });
      continue;
    }    if (item.role === "user") {
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
      const imageExt = /\.(png|jpe?g|webp|gif|bmp)$/i;
      const videoExt = /\.(mp4|webm|mov)$/i;
      const textExt = /\.(txt|md|markdown|ts|tsx|js|mjs|cjs|jsx|json|py|toml|css|html|htm|yml|yaml|xml|sh|bash|bat|ps1|rs|go|java|kt|sql|csv|log|ini|cfg|vue|svelte)$/i;
      for (const rawPath of item.inline_images ?? []) {
        const agentPath = rawPath.trim().replace(/\\/g, "/");
        if (!agentPath || !imageExt.test(agentPath)) continue;
        blocks.push({ kind: "generated-image", agentPath, turnIndex: assistantTurn });
      }
      for (const rawPath of item.inline_videos ?? []) {
        const agentPath = rawPath.trim().replace(/\\/g, "/");
        if (!agentPath || !videoExt.test(agentPath)) continue;
        blocks.push({ kind: "generated-video", agentPath, turnIndex: assistantTurn });
      }
      for (const rawPath of item.inline_files ?? []) {
        const agentPath = rawPath.trim().replace(/\\/g, "/");
        if (!agentPath || !textExt.test(agentPath)) continue;
        blocks.push({ kind: "inline-file", agentPath, turnIndex: assistantTurn });
      }
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
    pendingToolPrompt: null,
    currentTurnKey: "",
    turnCounter: 0,
    blocks: [],
    assistantBuffer: "",
    turnActive: false,
    turnFinished: false,
    turnActivityStartedAt: 0,
    toolsRunning: 0,
    executionRunId: "",
    executionState: "idle",
    executionSequence: 0,
    executionFinishReason: null,
    _toolTimers: new Map<string, number>(),
  };

  let cancelWatchdog: ReturnType<typeof setTimeout> | null = null;
  let deferredHistory: Extract<ServerEvent, { type: "session.history" }> | null = null;
  const acceptedCallIds = new Set<string>();
  // Scoped, single-dialogue post-turn confirmations never own the active turn.
  const postTurnConfirmations = new Map<string, { sessionId: string; done: boolean; subtype?: "work_record_reply_excerpt" | "work_record_auto_candidate" }>();
  const requirementVerificationConfirmations = new Map<string, { sessionId: string; runId: string; done: boolean; payload: unknown; preview: string }>();
  const postTurnResults = new Set<string>();
  const controlConfirmations = new Set<string>();

  let turnKeySeq = 0;
  /** Unique per turn: two turns started in the same millisecond used to share one 过程 key. */
  function nextTurnKey(): string {
    turnKeySeq += 1;
    return `turn-${Date.now()}-${turnKeySeq}`;
  }

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
    model.turnActivityStartedAt = Date.now();
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
    model.turnActivityStartedAt = 0;
    model.toolsRunning = 0;
    model.confirmSubmitting = false;
    model.cancelRequested = false;
  }

  function turnIsBusy(): boolean {
    return (
      model.turnActive
      || model.toolsRunning > 0
      || model.executionState === "queued"
      || model.executionState === "running"
      || model.executionState === "stopping"
    );
  }

  function latestTooledProcess(): (ChatBlock & { kind: "process" }) | undefined {
    for (let i = model.blocks.length - 1; i >= 0; i--) {
      const block = model.blocks[i];
      if (block.kind !== "process") continue;
      const hasTool =
        (block.tools?.length ?? 0) > 0
        || (block.entries ?? []).some((entry) => entry.kind === "tool");
      if (hasTool) return block;
    }
    return undefined;
  }

  /** The user block that opened the turn a process block belongs to. */
  function ownerUserOf(
    proc: ChatBlock & { kind: "process" },
  ): (ChatBlock & { kind: "user" }) | undefined {
    const idx = model.blocks.indexOf(proc);
    for (let i = idx - 1; i >= 0; i--) {
      const block = model.blocks[i];
      if (block.kind === "user") return block;
      if (block.kind === "review-subagent") return undefined;
    }
    return undefined;
  }

  /**
   * Re-insert a kept process block at the end of its own turn (history drops process blocks).
   * Appending it blindly attached an older turn's steps to the newest turn card.
   */
  function insertProcessIntoOwnerTurn(
    proc: ChatBlock & { kind: "process" },
    owner: (ChatBlock & { kind: "user" }) | undefined,
    liveTurn: number,
  ): boolean {
    let start = -1;
    if (owner) {
      for (let i = model.blocks.length - 1; i >= 0; i--) {
        const block = model.blocks[i];
        if (block.kind === "user" && block.text === owner.text) {
          start = i;
          break;
        }
      }
    }
    if (start < 0) {
      if (owner && owner.turnIndex !== liveTurn) return false;
      model.blocks.push(proc);
      return true;
    }
    let end = model.blocks.length;
    for (let i = start + 1; i < model.blocks.length; i++) {
      const kind = model.blocks[i].kind;
      if (kind === "user" || kind === "review-subagent") {
        end = i;
        break;
      }
    }
    model.blocks.splice(end, 0, proc);
    return true;
  }

  function findProcessByCallId(callId: string): (ChatBlock & { kind: "process" }) | undefined {
    for (let i = model.blocks.length - 1; i >= 0; i--) {
      const block = model.blocks[i];
      if (block.kind !== "process") continue;
      if (findToolEntry(block, callId)) return block;
    }
    return undefined;
  }

  function applySessionHistory(event: Extract<ServerEvent, { type: "session.history" }>): void {
    const sameSession =
      !event.session_id || !model.sessionId || event.session_id === model.sessionId;
    const keptProcess = sameSession ? latestTooledProcess() : undefined;
    const keptOwner = keptProcess ? ownerUserOf(keptProcess) : undefined;
    const liveTurn = model.turnCounter;
    const keptUsers = sameSession
      ? model.blocks.filter(
          (block): block is ChatBlock & { kind: "user" } =>
            block.kind === "user" && (block.turnIndex === liveTurn || Boolean(block.persistence)),
        )
      : [];
    const keptAssistants = sameSession
      ? model.blocks.filter(
          (block): block is ChatBlock & { kind: "assistant" } =>
            block.kind === "assistant" && block.turnIndex === liveTurn,
        )
      : [];
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
    for (const user of keptUsers) {
      const present = model.blocks.some((block) => block.kind === "user" && (user.persistence
        ? block.persistence?.requestId === user.persistence.requestId && block.persistence.sessionId === user.persistence.sessionId
        : block.text === user.text));
      if (!present) model.blocks.push(user);
    }
    if (keptProcess) {
      if (insertProcessIntoOwnerTurn(keptProcess, keptOwner, liveTurn)) {
        model.currentTurnKey = keptProcess.turnKey;
      }
    }
    for (const assistant of keptAssistants) {
      const present = model.blocks.some(
        (block) => block.kind === "assistant" && block.text === assistant.text,
      );
      if (!present) model.blocks.push(assistant);
    }
    hooks.onHistoryLoaded?.(loaded);
    notify();
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
    if (options.confirmInBlocks || postTurnConfirmations.has(requestId) || requirementVerificationConfirmations.has(requestId)) {
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
      if (block.kind === "confirm" && !postTurnConfirmations.has(block.requestId) && !requirementVerificationConfirmations.has(block.requestId) && !block.resolved && block.requestId !== exceptRequestId) {
        block.resolved = "已过期";
      }
    }
  }

  function finalizeInProgressConfirms(terminalLabel: string): void {
    if (options.confirmInBlocks) {
      for (const block of model.blocks) {
        if (block.kind === "confirm" && !postTurnConfirmations.has(block.requestId) && !requirementVerificationConfirmations.has(block.requestId) && isConfirmInProgressLabel(block.resolved)) {
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
    const verification = requirementVerificationConfirmations.get(requestId);
    if (verification) {
      const block = model.blocks.find((item) => item.kind === "confirm" && item.requestId === requestId);
      const parsed = block?.kind === "confirm" ? parseProjectWorkerVerification(block.projectWorkerVerification, model.sessionId) : null;
      const original = parseProjectWorkerVerification(verification.payload, verification.sessionId);
      if (verification.sessionId !== model.sessionId || verification.runId !== normalizedRunId(model.executionRunId) || model.cancelRequested || !["queued", "running"].includes(model.executionState) || verification.done || block?.kind !== "confirm" || block.resolved
          || choice === "a" || (choice !== "n" && (!block.preview.trim() || block.preview !== verification.preview || !parsed || !original || Object.keys(original).some(key => parsed[key as keyof typeof parsed] !== original[key as keyof typeof original])))) return false;
      markConfirmResolved(requestId, choice === "n" ? "提交拒绝…" : "提交中…");
      notify();
      return true;
    }
    const postTurn = postTurnConfirmations.get(requestId);
    if (postTurn) {
      const block = model.blocks.find((item) => item.kind === "confirm" && item.requestId === requestId);
      if (postTurn.sessionId !== model.sessionId || postTurn.done || block?.kind !== "confirm" || block.resolved
          || (postTurn.subtype !== undefined && choice === "a")
          || (postTurn.subtype === "work_record_auto_candidate" && choice !== "n" && !parseWorkRecordAutoCandidate(block.workRecordCandidate, model.sessionId))) {
        return false;
      }
      markConfirmResolved(requestId, choice === "n" ? "提交拒绝…" : "提交中…");
      notify();
      return true;
    }
    const currentId = model.confirmOverlay?.requestId;
    if (!currentId || requestId !== currentId) {
      return false;
    }
    if (model.confirmOverlay?.resolved) {
      return false;
    }
    const pendingLabel = choice === "n" ? "提交拒绝…" : "提交中…";
    markConfirmResolved(requestId, pendingLabel);
    if (controlConfirmations.has(requestId)) {
      notify();
      return true;
    }
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
      && !["queued", "running"].includes(model.executionState)
    ) {
      return false;
    }
    model.cancelRequested = true;
    for (const [requestId, verification] of requirementVerificationConfirmations) {
      if (!verification.done) {
        verification.done = true;
        markConfirmResolved(requestId, "已取消");
      }
    }
    model.confirmSubmitting = false;
    if (model.confirmOverlay?.requestId) {
      markConfirmResolved(model.confirmOverlay.requestId, "取消中…");
    }
    clearCancelWatchdog();
    cancelWatchdog = setTimeout(() => {
      if (!model.cancelRequested) return;
      model.blocks.push({
        kind: "notice",
        text: "仍未收到停止终态；当前工作状态未确认，继续等待服务端回执。",
      });
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
    model.currentTurnKey = nextTurnKey();
    model.assistantBuffer = "";
    beginTurnActivity();
  }

  function beginTurn(): number {
    finalizeInFlightProcessBlocks();
    model.currentTurnKey = nextTurnKey();
    model.turnCounter += 1;
    model.assistantBuffer = "";
    beginTurnActivity();
    return model.turnCounter;
  }

  function pushUserMessage(
    text: string,
    tracking?: { requestId: string; sessionId: string | null; receiptEligible: boolean; hasAttachments?: boolean },
  ): number {
    const turnIndex = beginTurn();
    const persistence = tracking
      ? {
          requestId: tracking.requestId,
          sessionId: tracking.sessionId,
          state: tracking.sessionId ? "pending" as const : "unknown" as const,
          receiptEligible: Boolean(tracking.sessionId) && tracking.receiptEligible,
          attachmentRetention: tracking.hasAttachments ? "unknown" as const : "not_applicable" as const,
        }
      : undefined;
    model.blocks.push({ kind: "user", text, turnIndex, ...(persistence ? { persistence } : {}) });
    notify();
    return turnIndex;
  }

  function addParallelChatRequest(text: string, requestId: string, workRunId: string): boolean {
    const normalizedText = text.trim();
    const normalizedRequestId = requestId.trim();
    const normalizedWorkRunId = normalizedRunId(workRunId);
    if (!normalizedText || !USER_MESSAGE_REQUEST_UUID.test(normalizedRequestId)
      || !normalizedWorkRunId || normalizedWorkRunId !== normalizedRunId(model.executionRunId)
      || model.executionState !== "running" || model.confirmPending || model.cancelRequested
      || model.blocks.some((block) => block.kind === "parallel-chat" && block.requestId === normalizedRequestId)) {
      return false;
    }
    model.blocks.push({
      kind: "parallel-chat",
      requestId: normalizedRequestId,
      sessionId: model.sessionId,
      workRunId: normalizedWorkRunId,
      promptText: normalizedText,
      responseText: "",
      deliveryState: "pending",
    });
    notify();
    return true;
  }

  function addWorkInstructionRequest(text: string, requestId: string, sessionId: string, workRunId: string): boolean {
    const normalizedText = text.trim();
    const normalizedRequestId = requestId.trim();
    const normalizedSessionId = sessionId.trim();
    const normalizedWorkRunId = normalizedRunId(workRunId);
    if (!normalizedText || !USER_MESSAGE_REQUEST_UUID.test(normalizedRequestId)
      || !normalizedSessionId || normalizedSessionId !== model.sessionId
      || !normalizedWorkRunId || normalizedWorkRunId !== normalizedRunId(model.executionRunId)
      || model.executionState !== "running" || model.confirmPending || model.cancelRequested
      || model.blocks.some((block) => block.kind === "work-instruction" && block.requestId === normalizedRequestId)) return false;
    model.blocks.push({ kind: "work-instruction", requestId: normalizedRequestId, sessionId: normalizedSessionId,
      workRunId: normalizedWorkRunId, text: normalizedText, state: "sending" });
    notify();
    return true;
  }

  function handleCompanionOrchestrationState(
    event: Extract<ServerEvent, { type: "companion.orchestration.state" }>,
  ): boolean {
    if (event.session_id !== model.sessionId || typeof event.request_id !== "string" || typeof event.run_id !== "string") {
      return false;
    }
    const block = model.blocks.find((item) => item.kind === "work-instruction" && item.requestId === event.request_id
      && item.sessionId === event.session_id && item.workRunId === event.run_id);
    if (!block || block.kind !== "work-instruction") return false;
    if (event.state === "dispatching") {
      if (block.state !== "sending" && block.state !== "orchestration_dispatching") return false;
      block.state = "orchestration_dispatching";
      block.reason = undefined;
      block.deliveryStage = undefined;
    } else if (event.state === "active") {
      if (block.state !== "orchestration_dispatching" && block.state !== "orchestration_active" && block.state !== "delivered") {
        return false;
      }
      block.state = "orchestration_active";
      block.reason = undefined;
    } else if (event.state === "undelivered") {
      if (block.state === "delivered" || block.state === "undelivered") return false;
      block.state = "undelivered";
      block.reason = typeof event.reason === "string" ? event.reason : undefined;
      block.deliveryStage = undefined;
    } else {
      return false;
    }
    notify();
    return true;
  }

  function handleWorkInstructionState(event: Extract<ServerEvent, { type: "work.instruction.state" }>): boolean {
    if (event.session_id !== model.sessionId || typeof event.request_id !== "string" || typeof event.run_id !== "string") return false;
    const block = model.blocks.find((item) => item.kind === "work-instruction" && item.requestId === event.request_id
      && item.sessionId === event.session_id && item.workRunId === event.run_id);
    if (!block || block.kind !== "work-instruction") return false;
    if (event.state === "queued") {
      if (block.state !== "sending" && block.state !== "queued") return false;
      block.state = "queued";
      block.reason = undefined;
      block.deliveryStage = undefined;
    } else if (event.state === "delivered") {
      const allowed = block.state === "queued" || block.state === "delivered"
        || block.state === "orchestration_dispatching" || block.state === "orchestration_active";
      if (event.delivery_stage !== "next_model_request_context" || !allowed) return false;
      block.state = block.state === "orchestration_dispatching" || block.state === "orchestration_active"
        ? "orchestration_active" : "delivered";
      block.deliveryStage = event.delivery_stage;
      block.reason = undefined;
    } else if (event.state === "undelivered") {
      if (block.state === "delivered" || block.state === "undelivered") return false;
      block.state = "undelivered";
      block.reason = typeof event.reason === "string" ? event.reason : undefined;
      block.deliveryStage = undefined;
    } else {
      return false;
    }
    notify();
    return true;
  }

  function requestParallelChatCancel(requestId: string): boolean {
    const block = model.blocks.find((item) => item.kind === "parallel-chat" && item.requestId === requestId);
    if (!block || block.kind !== "parallel-chat" || block.deliveryState !== "delivered" || block.cancelRequested) return false;
    block.cancelRequested = true;
    notify();
    return true;
  }

  function handleParallelChatEvent(event: Extract<ServerEvent, { type: "chat.start" | "chat.delta" | "chat.done" | "chat.error" | "chat.cancel.result" }>): boolean {
    if (typeof event.request_id !== "string") return false;
    let block = event.type === "chat.cancel.result"
      ? model.blocks.find((item) => item.kind === "parallel-chat" && item.requestId === event.request_id && item.sessionId === model.sessionId)
      : event.session_id === model.sessionId && typeof event.work_run_id === "string"
        ? model.blocks.find((item) => item.kind === "parallel-chat"
          && item.requestId === event.request_id && item.sessionId === event.session_id
          && item.workRunId === event.work_run_id)
        : undefined;
    if (!block && (event.type === "chat.start" || event.type === "chat.error")
      && event.session_id === model.sessionId && typeof event.work_run_id === "string") {
      const pendingIndex = model.blocks.findIndex((item) => item.kind === "work-instruction"
        && item.requestId === event.request_id && item.sessionId === event.session_id
        && item.workRunId === event.work_run_id);
      const pending = model.blocks[pendingIndex];
      if (pending?.kind === "work-instruction" && pending.state === "sending") {
        block = {
          kind: "parallel-chat", requestId: pending.requestId, sessionId: pending.sessionId,
          workRunId: pending.workRunId, promptText: pending.text, responseText: "", deliveryState: "pending",
        };
        model.blocks[pendingIndex] = block;
      }
    }
    if (!block || block.kind !== "parallel-chat") return false;
    if (event.type === "chat.cancel.result") {
      if (block.deliveryState !== "delivered" || typeof event.accepted !== "boolean") return false;
      block.cancelRequested = event.accepted;
    } else if (event.type === "chat.start") {
      if (block.deliveryState !== "pending") return false;
      block.deliveryState = "delivered";
    } else if (event.type === "chat.delta") {
      if (block.deliveryState !== "delivered" || typeof event.text !== "string") return false;
      block.responseText += event.text;
    } else if (event.type === "chat.done") {
      if (block.deliveryState !== "delivered" || typeof event.text !== "string") return false;
      block.deliveryState = "done";
      block.responseText = event.text;
    } else {
      if (block.deliveryState === "done" || block.deliveryState === "error"
        || typeof event.message !== "string" || typeof event.code !== "string") return false;
      block.deliveryState = "error";
      block.cancelRequested = false;
      block.errorCode = event.code;
      block.errorMessage = event.message;
    }
    notify();
    return true;
  }
  function acceptToolPromptRequest(event: Extract<ServerEvent, { type: "prompt.request" }>): boolean {
    const keys = Object.keys(event);
    if (keys.length !== 5 || keys.some((key) => !["type", "request_id", "run_id", "session_id", "prompt"].includes(key))
      || typeof event.request_id !== "string" || !USER_MESSAGE_REQUEST_UUID.test(event.request_id)
      || typeof event.run_id !== "string" || !event.run_id || event.run_id !== model.executionRunId
      || typeof event.session_id !== "string" || !event.session_id || event.session_id !== model.sessionId
      || typeof event.prompt !== "string" || !["running"].includes(model.executionState)) return false;
    const existing = model.pendingToolPrompt;
    if (existing) {
      return existing.requestId === event.request_id && existing.runId === event.run_id && existing.sessionId === event.session_id;
    }
    const block: ToolPromptBlock = {
      kind: "tool-prompt", requestId: event.request_id, runId: event.run_id, sessionId: event.session_id,
      prompt: event.prompt, responseText: "", state: "waiting",
    };
    model.blocks.push(block);
    model.pendingToolPrompt = block;
    notify();
    return true;
  }

  function beginToolPromptResponse(requestId: string, runId: string, sessionId: string, text: string): boolean {
    const pending = model.pendingToolPrompt;
    if (!pending || pending.requestId !== requestId || pending.runId !== runId || pending.sessionId !== sessionId
      || sessionId !== model.sessionId || runId !== model.executionRunId || model.executionState !== "running"
      || model.cancelRequested || model.confirmPending || typeof text !== "string" || text.length > 4000
      || (pending.state !== "waiting" && pending.state !== "rejected")) return false;
    pending.responseText = text;
    pending.state = "sending";
    pending.reason = undefined;
    notify();
    return true;
  }

  function handlePromptResponseResult(event: Extract<ServerEvent, { type: "prompt.response.result" }>): boolean {
    const keys = Object.keys(event);
    const pending = model.pendingToolPrompt;
    if (keys.length !== 5 || keys.some((key) => !["type", "request_id", "run_id", "session_id", "accepted"].includes(key))
      || typeof event.accepted !== "boolean" || !pending || pending.state !== "sending"
      || event.request_id !== pending.requestId || event.run_id !== pending.runId || event.session_id !== pending.sessionId
      || event.session_id !== model.sessionId || event.run_id !== model.executionRunId) return false;
    if (event.accepted) {
      pending.state = "accepted";
      model.pendingToolPrompt = null;
    } else {
      pending.state = "rejected";
      pending.reason = "后台未接受；回复已保留，且未发送为普通消息";
    }
    notify();
    return true;
  }

  function expirePendingToolPrompt(reason: string): void {
    const pending = model.pendingToolPrompt;
    if (!pending) return;
    pending.state = "rejected";
    pending.reason = reason;
    model.pendingToolPrompt = null;
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

  /** Turn is over (done / error / stopped): stop live indicators and fold this turn's 过程. */
  function settleCurrentTurnProcesses(): void {
    let start = 0;
    for (let i = model.blocks.length - 1; i >= 0; i--) {
      const kind = model.blocks[i].kind;
      if (kind === "user" || kind === "review-subagent") {
        start = i;
        break;
      }
    }
    for (let i = start; i < model.blocks.length; i++) {
      const block = model.blocks[i];
      if (block.kind !== "process") continue;
      finalizeProcessAfterTurn(block);
      block.collapsed = true;
    }
    const current = findProcessBlock(model.currentTurnKey);
    if (current) {
      finalizeProcessAfterTurn(current);
      current.collapsed = true;
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

  function isPendingOrdinaryConfirmRequest(requestId: string): boolean {
    if (!requestId.trim()) return false;
    if (model.confirmOverlay?.requestId === requestId) return true;
    if (!options.confirmInBlocks) return model.confirmPending;
    return model.blocks.some(
      (block) => block.kind === "confirm"
        && block.requestId === requestId
        && !block.resolved
        && !postTurnConfirmations.has(block.requestId)
        && !requirementVerificationConfirmations.has(block.requestId),
    );
  }

  function isStaleRunEvent(event: ServerEvent): boolean {
    if (
      event.type === "confirm.done"
      && typeof event.request_id === "string"
      && isPendingOrdinaryConfirmRequest(event.request_id)
    ) {
      return false;
    }
    if (
      (event.type === "tool.end" || event.type === "tool.progress" || event.type === "tool.start")
      && acceptedCallIds.has(event.call_id)
    ) {
      return false;
    }
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
      "prompt.request",
      "prompt.response.result",
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

  function markPendingUserMessagesUnknown(sessionId: string): void {
    if (!sessionId) return;
    for (const block of model.blocks) {
      if (block.kind === "user" && block.persistence?.sessionId === sessionId && block.persistence.state === "pending") {
        block.persistence = { ...block.persistence, state: "unknown" };
      }
    }
  }

  function acceptPersistedUserMessage(event: Extract<ServerEvent, { type: "user.message.persisted" }>): boolean {
    if (Object.keys(event).length !== 3
      || Object.keys(event).some((key) => key !== "type" && key !== "session_id" && key !== "request_id")
      || typeof event.session_id !== "string" || !event.session_id || event.session_id !== model.sessionId
      || typeof event.request_id !== "string" || !USER_MESSAGE_REQUEST_UUID.test(event.request_id)) return false;
    const matches = model.blocks.filter((block) => block.kind === "user"
      && block.persistence?.requestId === event.request_id
      && block.persistence.sessionId === event.session_id
      && block.persistence.receiptEligible);
    if (matches.length !== 1) return false;
    const block = matches[0];
    if (block.kind !== "user" || !block.persistence || block.persistence.state === "saved") return false;
    block.persistence = { ...block.persistence, state: "saved" };
    notify();
    return true;
  }

  function handleEvent(event: ServerEvent): boolean {
    if (isStaleRunEvent(event)) return false;
    switch (event.type) {
      case "prompt.request":
        return acceptToolPromptRequest(event);
      case "prompt.response.result":
        return handlePromptResponseResult(event);
      case "chat.start":
      case "chat.delta":
      case "chat.done":
      case "chat.error":
      case "chat.cancel.result":
        return handleParallelChatEvent(event);
      case "work.instruction.state":
        return handleWorkInstructionState(event);
      case "companion.orchestration.state":
        return handleCompanionOrchestrationState(event);
      case "execution.stop.result": {
        const runId = normalizedRunId(event.run_id);
        if (!runId || runId !== normalizedRunId(model.executionRunId)) return false;
        if (!event.accepted && !model.confirmPending && model.cancelRequested
          && ["queued", "running"].includes(model.executionState)) {
          model.cancelRequested = false;
          clearCancelWatchdog();
        }
        notify();
        break;
      }
      case "user.message.persisted": {
        if (!acceptPersistedUserMessage(event)) return false;
        break;
      }
      case "client.connection.closed": {
        markPendingUserMessagesUnknown(model.sessionId);
        for (const [requestId, verification] of requirementVerificationConfirmations) {
          if (!verification.done) {
            verification.done = true;
            markConfirmResolved(requestId, "连接已断开");
          }
        }
        notify();
        break;
      }
      case "session.banner": {
        if (model.sessionId && model.sessionId !== event.session_id) markPendingUserMessagesUnknown(model.sessionId);
        if (model.sessionId !== event.session_id) { postTurnConfirmations.clear(); requirementVerificationConfirmations.clear(); postTurnResults.clear(); controlConfirmations.clear(); }
        const sessionChanged = Boolean(model.sessionId) && model.sessionId !== event.session_id;
        if (model.pendingToolPrompt && (sessionChanged || model.pendingToolPrompt.sessionId !== event.session_id)) {
          expirePendingToolPrompt("会话已切换；工具输入未发送为普通消息");
        }
        if (!sessionChanged && (turnIsBusy() || model.confirmPending || model.confirmSubmitting)) {
          model.sessionId = event.session_id;
          hooks.onSessionBanner?.(event.session_id);
          notify();
          break;
        }
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
        if (turnIsBusy()) {
          deferredHistory = event;
          break;
        }
        deferredHistory = null;
        applySessionHistory(event);
        break;
      }
      case "execution.state": {
        if (event.sequence < model.executionSequence) return false;
        const runId = normalizedRunId(event.run_id);
        for (const [requestId, verification] of requirementVerificationConfirmations) {
          if (!verification.done && (verification.runId !== runId || !["queued", "running"].includes(event.state))) {
            verification.done = true;
            markConfirmResolved(requestId, event.state === "stopping" ? "已取消" : verification.runId === runId ? "执行已结束" : "已过期");
          }
        }
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
          if (model.pendingToolPrompt && model.pendingToolPrompt.runId === runId) {
            expirePendingToolPrompt("工作已结束；工具输入未发送为普通消息");
          }
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
        const existing = model.currentTurnKey ? findProcessBlock(model.currentTurnKey) : undefined;
        const reuse = model.turnActive && Boolean(existing);
        if (reuse && existing) {
          existing.collapsed = false;
          beginTurnActivity();
        } else {
          if (runId) model.executionRunId = runId;
          beginServerProcessTurn();
        }
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
        markPendingUserMessagesUnknown(model.sessionId);
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
          settleCurrentTurnProcesses();
        }
        finalizeInProgressConfirms(event.finish_reason === "cancelled" ? "已取消" : "已完成执行");
        resetTurnActivity();
        model._toolTimers.clear();
        model.confirmPending = false;
        model.confirmSubmitting = false;
        const pendingHistory = deferredHistory;
        deferredHistory = null;
        if (pendingHistory) {
          applySessionHistory(pendingHistory);
        }
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
      case "tool.start": {
        const seen = acceptedCallIds.has(event.call_id);
        acceptedCallIds.add(event.call_id);
        if (!seen) {
          model.toolsRunning += 1;
          model._toolTimers.set(event.call_id, Date.now());
        }
        const owner = findProcessByCallId(event.call_id);
        if (options.showProcess && !owner) {
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
      }
      case "tool.end":
        acceptedCallIds.add(event.call_id);
        model.toolsRunning = Math.max(0, model.toolsRunning - 1);
        if (options.showProcess) {
          const startTime = model._toolTimers.get(event.call_id);
          const now = Date.now();
          const started = startTime ?? now;
          const proc = findProcessByCallId(event.call_id) ?? ensureActiveProcessBlock();
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
            card.status = settledToolStatus(event.ok, event.summary, event.note);
            card.endedAt = now;
            card.endSummary = event.summary || (event.ok ? "完成" : "失败");
            if (typeof event.logs_tail === "string" && event.logs_tail.trim()) {
              card.logsTail = event.logs_tail.trim();
            }
            if (event.path?.trim()) {
              card.path = event.path.trim();
            }
            if (hasStringDiff(event) && event.diff?.trim()) {
              card.diff = event.diff;
            }
            syncProcessLegacyFields(proc);
          }
          model._toolTimers.delete(event.call_id);
        }
        if (model.turnFinished && model.toolsRunning === 0) {
          model.turnActive = false;
        }
        if (event.ok) {
          const displayPaths = normalizeDisplayPaths(event.display_paths);
          const imageExt = /\.(png|jpe?g|webp|gif|bmp)$/i;
          const videoExt = /\.(mp4|webm|mov)$/i;
          const textExt = /\.(txt|md|markdown|ts|tsx|js|mjs|cjs|jsx|json|py|toml|css|html|htm|yml|yaml|xml|sh|bash|bat|ps1|rs|go|java|kt|sql|csv|log|ini|cfg|vue|svelte)$/i;
          const pushMediaBlock = (agentPath: string) => {
            if (imageExt.test(agentPath)) {
              model.blocks.push({
                kind: "generated-image",
                agentPath,
                turnIndex: currentTurnIndex(),
              });
              return;
            }
            if (videoExt.test(agentPath)) {
              model.blocks.push({
                kind: "generated-video",
                agentPath,
                turnIndex: currentTurnIndex(),
              });
              return;
            }
            if (textExt.test(agentPath)) {
              model.blocks.push({
                kind: "inline-file",
                agentPath,
                turnIndex: currentTurnIndex(),
              });
            }
          };
          if (displayPaths.length) {
            for (const p of displayPaths) pushMediaBlock(p);
          } else if (/generate_image|show_images|generate_video/i.test(event.tool || "")) {
            const mediaPath = event.path?.trim() || event.output_path?.trim() || "";
            if (mediaPath) pushMediaBlock(mediaPath.replace(/\\/g, "/"));
          }
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
            (b): b is ChatBlock & { kind: "process" } =>
              b.kind === "process" && b.turnKey === model.currentTurnKey && !b.collapsed,
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
          if (proc && model.toolsRunning === 0 && !model.confirmPending) {
            finalizeProcessAfterTurn(proc);
            collapseCurrentProcess();
          }
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
      case "work-record.post-turn.result": {
        const messages: Record<string, string> = {
          recorded: "事后工作记录已保存并关联原回合；事项尚未验证。",
          already_recorded: "本条消息的工作记录此前已保存，本次未重复写入。",
          not_recorded: "本次事后记录未完成；不会重新执行原工作。",
          append_not_allowed: "候选需要追加已有事项，本次仅允许新建，未写入。",
          uncertain: "事后记录结果无法确认，请核对记录；不要重新执行原工作。",
        };
        if (!event.session_id || event.session_id !== model.sessionId ||
            typeof event.request_id !== "string" || !event.request_id.trim() ||
            !Object.hasOwn(messages, event.outcome) || postTurnResults.has(event.request_id)) return false;
        postTurnResults.add(event.request_id);
        const message = typeof event.message === "string" && event.message.trim() ? event.message : messages[event.outcome];
        model.blocks.push({ kind: "notice", text: message, plainText: true });
        notify();
        break;
      }
      case "notice":
        model.blocks.push({ kind: "notice", text: event.text });
        notify();
        break;
      case "error":
        markPendingUserMessagesUnknown(model.sessionId);
        if (options.showProcess) {
          // Error ends the turn: without this the pill kept "等待模型…" and refused to collapse.
          settleCurrentTurnProcesses();
        }
        model.blocks.push({ kind: "notice", text: event.message });
        resetTurnActivity();
        hooks.onError?.();
        notify();
        break;
      case "confirm.request": {
        if (event.confirm_subtype === "project_worker_requirement_verification") {
          const sessionId = typeof event.session_id === "string" ? event.session_id : "";
          const runId = normalizedRunId(event.run_id);
          const currentRunId = normalizedRunId(model.executionRunId);
          if (!sessionId || sessionId !== model.sessionId || !runId || !currentRunId || runId !== currentRunId || model.cancelRequested || !["queued", "running"].includes(model.executionState) || event.allow_approve_all !== false
              || typeof event.request_id !== "string" || !event.request_id.trim()
              || requirementVerificationConfirmations.has(event.request_id) || postTurnConfirmations.has(event.request_id)) return false;
          const payload = parseProjectWorkerVerification(event.project_worker_verification, sessionId);
          const preview = typeof event.preview === "string" ? event.preview : "";
          requirementVerificationConfirmations.set(event.request_id, { sessionId, runId, done: false, payload: payload ?? event.project_worker_verification, preview });
          model.blocks.push({
            kind: "confirm", confirmKind: "project_worker_requirement_verification",
            confirmSubtype: "project_worker_requirement_verification", sessionId,
            projectWorkerVerification: payload ?? event.project_worker_verification,
            requestId: event.request_id, preview: typeof event.preview === "string" ? event.preview : "", allowApproveAll: false,
          });
          notify();
          break;
        }
        if (event.session_id !== undefined && event.session_id !== model.sessionId) return false;
        if (event.project_worker_verification !== undefined || (event.confirm_subtype !== undefined
            && event.confirm_subtype !== "work_record_reply_excerpt" && event.confirm_subtype !== "work_record_auto_candidate")) return false;
        if (event.confirm_kind === "work_record_post_turn") {
          const sessionId = typeof event.session_id === "string" ? event.session_id : "";
          if (!sessionId || sessionId !== model.sessionId || typeof event.request_id !== "string" || !event.request_id.trim() || postTurnConfirmations.has(event.request_id)) {
            return false;
          }
          const subtype = event.confirm_subtype === "work_record_reply_excerpt" || event.confirm_subtype === "work_record_auto_candidate" ? event.confirm_subtype : undefined;
          postTurnConfirmations.set(event.request_id, { sessionId, done: false, subtype });
          model.blocks.push({
            kind: "confirm",
            confirmKind: "work_record_post_turn",
            ...(subtype ? { confirmSubtype: subtype, sessionId } : {}),
            ...(subtype === "work_record_auto_candidate" ? { workRecordCandidate: event.work_record_candidate } : {}),
            requestId: event.request_id,
            preview: subtype && typeof event.preview !== "string" ? "" : event.preview,
            allowApproveAll: subtype ? false : event.allow_approve_all,
          });
          notify();
          break;
        }
        const terminalExecution = Boolean(model.executionRunId)
          && !["queued", "running", "stopping"].includes(model.executionState);
        if (terminalExecution) controlConfirmations.add(event.request_id);
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
        const verification = requirementVerificationConfirmations.get(event.request_id);
        if (verification || event.confirm_subtype === "project_worker_requirement_verification") {
          if (!verification || verification.done || event.confirm_subtype !== "project_worker_requirement_verification"
              || event.session_id !== model.sessionId || event.session_id !== verification.sessionId
              || normalizedRunId(event.run_id) !== verification.runId
              || !model.blocks.some((block) => block.kind === "confirm" && block.requestId === event.request_id
                && block.confirmSubtype === "project_worker_requirement_verification")) return false;
          verification.done = true;
          markConfirmResolved(event.request_id, CONFIRM_LABELS[event.choice] ?? event.choice);
          hooks.onConfirmDone?.(event.choice);
          notify();
          break;
        }
        const postTurn = postTurnConfirmations.get(event.request_id);
        if (postTurn || event.confirm_kind === "work_record_post_turn") {
          if (
            !postTurn
            || postTurn.done
            || event.confirm_kind !== "work_record_post_turn"
            || event.session_id !== model.sessionId
            || event.session_id !== postTurn.sessionId
            || (postTurn.subtype !== undefined && event.confirm_subtype !== postTurn.subtype)
            || !model.blocks.some((block) => block.kind === "confirm" && block.requestId === event.request_id)
          ) {
            return false;
          }
          if (postTurn.subtype === "work_record_auto_candidate") {
            const block = model.blocks.find(item => item.kind === "confirm" && item.requestId === event.request_id);
            const original = block?.kind === "confirm" ? parseWorkRecordAutoCandidate(block.workRecordCandidate, postTurn.sessionId) : null;
            const completed = parseWorkRecordAutoCandidate(event.work_record_candidate, postTurn.sessionId);
            if (original && (!completed || original.title !== completed.title || original.requirement !== completed.requirement
              || original.operation !== completed.operation || original.item_id !== completed.item_id
              || original.request_id !== completed.request_id || original.run_id !== completed.run_id
              || original.sequence !== completed.sequence || original.terminal_state !== completed.terminal_state
              || original.finish_reason !== completed.finish_reason || original.scope.kind !== completed.scope.kind
              || (original.scope.kind === "project" && (completed.scope.kind !== "project" || original.scope.project_id !== completed.scope.project_id)))) return false;
          }
          postTurn.done = true;
          markConfirmResolved(event.request_id, CONFIRM_LABELS[event.choice] ?? event.choice);
          notify();
          break;
        }
        if (event.session_id !== undefined && event.session_id !== model.sessionId) return false;
        const isCurrent = model.confirmOverlay?.requestId === event.request_id;
        controlConfirmations.delete(event.request_id);
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
        if (!rejected && ["queued", "running", "stopping"].includes(model.executionState)) {
          beginTurnActivity();
        }
        hooks.onConfirmDone?.(event.choice);
        notify();
        break;
      }
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
    beginToolPromptResponse,
    addParallelChatRequest,
    addWorkInstructionRequest,
    requestParallelChatCancel,
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

